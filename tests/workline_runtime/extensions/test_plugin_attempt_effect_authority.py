"""插件 attempt 的真实执行身份与 BusinessReject 原子回流。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from src.app.runtime.orchestration.effect_result import RuntimeIntentEffectResult


class _RepoChainEffectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str


class _RepoChainEffectOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    accepted: bool


class _RepoChainHandler:
    async def __call__(self, _request: _RepoChainEffectInput, *, execution: object) -> _RepoChainEffectOutput:
        _ = execution
        return _RepoChainEffectOutput(accepted=True)


@pytest.mark.asyncio
async def test_plugin_attempt_lock_loads_effect_execution_identity() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
    from src.app.runtime.orchestration.execution_session import ExecutionSession
    from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
    from src.app.runtime.orchestration.models.session import WorklineSession
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import PluginAttemptRepository
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
    from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
        SystemCapabilityIntentService,
    )
    from src.app.runtime.system_capabilities.definition import (
        EffectCompletionMode,
        SystemCapabilityDefinition,
        SystemCapabilityMode,
    )
    from src.app.workline.models.plugin_binding import WorklinePluginBinding

    class EffectRepository:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def claim_or_match(self, _db: object, **kwargs: object) -> ClaimResult:
            self.calls.append(kwargs)
            return ClaimResult.NEW

    digest = "d" * 64
    config_hash = "c" * 64
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach_schema(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_biz")  # type: ignore[attr-defined]
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS wes_runtime")  # type: ignore[attr-defined]

    tables = (
        ExecutionSession.__table__,
        ExecutionCorrelation.__table__,
        WorklinePluginBinding.__table__,
        WorklineSession.__table__,
        RuntimeInbox.__table__,
        ExecutionWorkItem.__table__,
    )
    async with engine.begin() as connection:
        for table in tables:
            await connection.run_sync(table.create)

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        binding = WorklinePluginBinding(
            workline_id=8,
            plugin_key="test_plugin",
            contract_version="v1",
            binding_version=1,
            typed_config_hash=config_hash,
            generated_index_digest=digest,
            environment="test",
            activated_at=datetime.now(UTC).replace(tzinfo=None),
            activated_by="pytest",
            activated_reason="repository chain",
        )
        db.add(binding)
        await db.flush()
        execution_session = ExecutionSession(
            workline_id=8,
            manifest_version="manifest-v1",
            plugin_key="test_plugin",
            plugin_binding_id=binding.id,
            plugin_binding_version=1,
            plugin_config_hash=config_hash,
            plugin_index_digest=digest,
        )
        db.add(execution_session)
        await db.flush()
        db.add(
            ExecutionCorrelation(
                correlation_id="corr-real-1",
                execution_session_id=execution_session.id,
                trace_id="trace-real-1",
            )
        )
        session = WorklineSession(
            session_code="SESSION-REAL-LOCK",
            workline_id=8,
            plugin_key="test_plugin",
            contract_version="v1",
            plugin_binding_id=binding.id,
            plugin_binding_version=1,
            plugin_config_hash=config_hash,
            plugin_index_digest=digest,
        )
        db.add(session)
        await db.flush()
        work_item = ExecutionWorkItem(
            execution_session_id=execution_session.id,
            correlation_id="corr-real-1",
            plugin_key="test_plugin",
            manifest_version="manifest-v1",
            plugin_binding_id=binding.id,
            plugin_binding_version=1,
            plugin_config_hash=config_hash,
            plugin_index_digest=digest,
            object_type="material",
            object_key="material:31",
            current_step="scan",
        )
        db.add(work_item)
        inbox = RuntimeInbox(
            execution_session_id=execution_session.id,
            workline_session_id=session.id,
            correlation_id="corr-real-1",
            kind="INTERNAL_EVENT",
            workline_id=8,
            provider_code="RUNTIME",
            event_type="SCAN_COMPLETED",
            source_event_id="event-real-1",
            payload_hash="e" * 64,
            payload_json={"logical_route": "SCAN_COMPLETED"},
            payload_schema_version=1,
            claim_bucket_key="session:1",
            received_at=1_700_000_000_000,
        )
        db.add(inbox)
        await db.commit()

        locked = await PluginAttemptRepository().lock_authoritative(
            db,
            inbox_id=int(inbox.id),
            session_id=int(session.id),
        )

        assert locked is not None
        assert locked.inbox is inbox
        assert locked.work_item is work_item
        assert locked.plugin_binding is binding
        definition = SystemCapabilityDefinition(
            capability_key="test.effect",
            contract_version="v1",
            mode=SystemCapabilityMode.EFFECT,
            input_model=_RepoChainEffectInput,
            output_model=_RepoChainEffectOutput,
            handler_factory=_RepoChainHandler,
            required_ports=(),
            admission="runtime",
            timeout_seconds=1,
            completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            audit_policy="metadata",
        )
        effect_repository = EffectRepository()
        service = SystemCapabilityIntentService(
            definitions={("test.effect", "v1"): definition},
            plugin_definitions={("test_plugin", "v1"): SimpleNamespace(allowed_capabilities=(("test.effect", "v1"),))},
            plugin_index_digest=digest,
            effect_repository=effect_repository,
        )
        await service.prepare_and_claim(
            {
                "db": db,
                "session": locked.session,
                "work_item": locked.work_item,
                "plugin_binding": locked.plugin_binding,
                "inbox": locked.inbox,
            },
            RuntimeIntent.system_capability(
                capability_key="test.effect",
                contract_version="v1",
                operation_key="operation-real-1",
                dispatch_key="system-capability:test.effect:operation-real-1",
                payload=_RepoChainEffectInput(value="A"),
                precondition={"expected": 1},
                fact_version="fact:1",
                timeout_seconds=1,
                creator_authority="WORKLINE_PLUGIN",
                authorization_policy="PLUGIN_DECLARED_CAPABILITY",
                binding_snapshot={"binding_id": binding.id, "binding_version": 1},
                provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
            ),
        )
        assert effect_repository.calls[0]["execution_work_item_id"] == work_item.id
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability_key", "original_route"),
    (
        ("material_flow.material_unit_write", "SCAN_COMPLETED"),
        ("device.device_command_write", "SCAN_COMPLETED"),
        ("runtime.session_hold", "SCAN_COMPLETED"),
        ("runtime.session_hold", "CAPABILITY_EFFECT_RESULT"),
    ),
)
async def test_business_reject_rolls_back_attempt_and_emits_typed_internal_result(
    capability_key: str,
    original_route: str,
) -> None:
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    events: list[str] = []
    pending_side_effects: list[str] = []
    snapshot = AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3)
    evidence = {
        "capability_key": capability_key,
        "contract_version": "v1",
        "operation_key": "operation-1",
        "idempotency_key": "effect-1",
        "payload_hash": "a" * 64,
        "outcome_kind": "business_reject",
        "outcome_code": "STALE_PRECONDITION",
        "outcome": {
            "kind": "business_reject",
            "reason_code": "STALE_PRECONDITION",
            "message": "fact changed",
            "details": {},
        },
        "occurred_at_ms": 1,
    }

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            pending_side_effects.clear()
            events.append("rollback")

    class PluginRepository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(
                    id=91,
                    processor_token="lease-1",
                    kind="INTERNAL_EVENT",
                    event_id="event-91",
                    trace_id="trace-1",
                    execution_session_id=21,
                    correlation_id="corr-1",
                    payload_json={"logical_route": original_route},
                ),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("MUST_NOT_PERSIST_STATE")

    class IntentRepository:
        def prepare_attempt_intents(self, **_kwargs: object) -> tuple[object, ...]:
            return (SimpleNamespace(model=object(), claim={"idempotency_key": "intent-1"}),)

        def add_prepared(self, *_args: object) -> None:
            pending_side_effects.append("ledger")
            events.append("ledger")

    class Guard:
        async def claim_or_match(self, *_args: object, **_kwargs: object) -> ClaimResult:
            return ClaimResult.NEW

    class RejectingEffectApplier:
        async def apply(self, *_args: object, **_kwargs: object) -> object:
            pending_side_effects.append("domain-write")
            events.append("effect-reject")
            return RuntimeIntentEffectResult.business_rejected(evidence)

    class InboxService:
        async def accept_internal_event(self, _db: object, **kwargs: object) -> object:
            assert original_route != "CAPABILITY_EFFECT_RESULT", "result redecision must not recurse"
            events.append("typed-feedback")
            assert kwargs["event_type"] == "CAPABILITY_EFFECT_RESULT"
            payload = kwargs["payload_json"]
            assert payload["logical_route"] == "CAPABILITY_EFFECT_RESULT"
            assert payload["data"] == {"session_id": 41, "effect_evidence": evidence}
            return SimpleNamespace(created=True)

        async def mark_processed(self, _db: object, **kwargs: object) -> bool:
            assert kwargs == {"inbox_id": 91, "lease_token": "lease-1"}
            events.append("terminal")
            return True

        async def mark_failed(self, _db: object, **kwargs: object) -> bool:
            assert original_route == "CAPABILITY_EFFECT_RESULT"
            assert kwargs["error_code"] == "CAPABILITY_EFFECT_REDECISION_REJECTED"
            assert kwargs["retryable"] is False
            events.append("terminal-failed")
            return True

    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=PluginRepository(),
        intent_log_repository=IntentRepository(),
        idempotency_guard=Guard(),
        effect_applier=RejectingEffectApplier(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=snapshot,
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        workline=SimpleNamespace(id=8),
        write_set=AttemptWriteSet(evidence=(), next_state={"phase": "MUST_NOT_ADVANCE"}, intents=("i1",)),
    )

    expected_disposition = "TERMINAL_FAILURE" if original_route == "CAPABILITY_EFFECT_RESULT" else "COMMITTED"
    assert disposition.disposition.value == expected_disposition
    assert pending_side_effects == []
    if original_route == "CAPABILITY_EFFECT_RESULT":
        assert events == ["ledger", "effect-reject", "rollback", "terminal-failed", "commit"]
    else:
        assert events == ["ledger", "effect-reject", "rollback", "typed-feedback", "terminal", "commit"]
