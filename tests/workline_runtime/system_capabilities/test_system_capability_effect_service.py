"""通用 SYSTEM_CAPABILITY EFFECT coordinator 合同。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult, IdempotencyConflict
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import (
    SystemCapabilityEffectService,
)
from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
    SystemCapabilityIntentService,
)
from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    accepted: bool


class _RecordingHandler:
    calls: ClassVar[list[tuple[_Input, object]]] = []

    async def __call__(self, request: _Input, *, execution: object) -> Success[_Output]:
        self.calls.append((request, execution))
        return Success(payload=_Output(accepted=True))


class _StaleHandler:
    calls: ClassVar[int] = 0
    stale: ClassVar[bool] = True

    async def __call__(self, request: _Input, *, execution: object) -> BusinessReject:
        _ = request, execution
        type(self).calls += 1
        if self.stale:
            return BusinessReject(reason_code="STALE_PRECONDITION", message="fact version changed")
        return Success(payload=_Output(accepted=True))  # type: ignore[return-value]


class _FailingHandler:
    async def __call__(self, request: _Input, *, execution: object) -> object:
        _ = request, execution
        raise RuntimeError("provider secret")


def _definition(
    handler: type[object] = _RecordingHandler,
    *,
    completion_mode: EffectCompletionMode = EffectCompletionMode.LOCAL_TRANSACTIONAL,
) -> SystemCapabilityDefinition:
    return SystemCapabilityDefinition(
        capability_key="test.effect",
        contract_version="v1",
        mode=SystemCapabilityMode.EFFECT,
        input_model=_Input,
        output_model=_Output,
        handler_factory=handler,
        required_ports=(),
        admission="runtime",
        timeout_seconds=1,
        completion_mode=completion_mode,
        audit_policy="metadata",
    )


class _EffectRepository:
    def __init__(self, result: ClaimResult | BaseException) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.outcomes: list[object] = []

    async def claim_or_match(self, _db: object, **kwargs: Any) -> ClaimResult:
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def record_outcome(self, _db: object, **kwargs: Any) -> None:
        self.outcomes.append(kwargs["evidence"])

    async def list_redecision_evidence(self, _db: object, **_kwargs: Any) -> tuple[object, ...]:
        return tuple(
            evidence for evidence in self.outcomes if getattr(evidence, "outcome_kind", None) == "business_reject"
        )


class _StatefulEffectRepository(_EffectRepository):
    def __init__(self) -> None:
        super().__init__(ClaimResult.NEW)
        self.status: str | None = None
        self.request_hash: str | None = None

    async def claim_or_match(self, _db: object, **kwargs: Any) -> ClaimResult:
        self.calls.append(kwargs)
        incoming = kwargs["request_hash"]
        if self.request_hash is not None and self.request_hash != incoming:
            raise IdempotencyConflict(
                provider_code=kwargs["provider_code"],
                operation_kind=kwargs["operation_kind"],
                idempotency_key=kwargs["idempotency_key"],
                existing_request_hash=self.request_hash,
                incoming_request_hash=incoming,
            )
        self.request_hash = incoming
        return ClaimResult.MATCH if self.status == "SUCCEEDED" else ClaimResult.NEW

    async def record_outcome(self, _db: object, **kwargs: Any) -> None:
        await super().record_outcome(_db, **kwargs)
        evidence = kwargs["evidence"]
        self.status = "SUCCEEDED" if evidence.outcome_kind == "success" else "BUSINESS_REJECT"


class _Db:
    def __init__(self) -> None:
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


class _MutationDb(_Db):
    def __init__(self) -> None:
        super().__init__()
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1
        for index, value in enumerate(self.added, start=1):
            if hasattr(value, "id") and getattr(value, "id", None) is None:
                value.id = 100 + index


def _intent(value: str = "A") -> RuntimeIntent:
    return RuntimeIntent.system_capability(
        capability_key="test.effect",
        contract_version="v1",
        operation_key="operation-1",
        payload=_Input(value=value),
        precondition={"expected": 3},
        fact_version="fact:3",
        timeout_seconds=1,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 9, "binding_version": 1},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )


def _ctx(db: _Db | None = None) -> dict[str, object]:
    pin = {
        "plugin_key": "test_plugin",
        "plugin_binding_id": 9,
        "plugin_binding_version": 1,
        "plugin_index_digest": "d" * 64,
    }
    return {
        "db": db or _Db(),
        "session": SimpleNamespace(id=31, contract_version="v1", **pin),
        "work_item": SimpleNamespace(id=41, **pin),
        "plugin_binding": SimpleNamespace(
            id=9,
            binding_version=1,
            plugin_key="test_plugin",
            contract_version="v1",
            generated_index_digest="d" * 64,
            is_enabled=True,
            is_revoked=False,
        ),
        "inbox": SimpleNamespace(correlation_id="corr-1", execution_session_id=21),
        "trace_id": "trace-1",
    }


def _service(definition: SystemCapabilityDefinition, repository: _EffectRepository) -> SystemCapabilityEffectService:
    plugin_definition = SimpleNamespace(
        plugin_key="test_plugin",
        contract_version="v1",
        allowed_capabilities=((definition.capability_key, definition.contract_version),),
    )
    intent_service = SystemCapabilityIntentService(
        definitions={(definition.capability_key, definition.contract_version): definition},
        plugin_definitions={("test_plugin", "v1"): plugin_definition},
        plugin_index_digest="d" * 64,
        effect_repository=repository,
    )
    return SystemCapabilityEffectService(intent_service=intent_service)


@pytest.mark.asyncio
async def test_local_transactional_effect_flushes_without_owning_commit_or_rollback() -> None:
    _RecordingHandler.calls.clear()
    definition = _definition()
    db = _Db()
    result = await _service(definition, _EffectRepository(ClaimResult.NEW)).apply(_ctx(db), _intent())

    assert isinstance(result.outcome, Success)
    assert result.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL
    assert result.durably_accepted is False
    assert len(_RecordingHandler.calls) == 1
    assert db.flush_count == 1
    assert db.commit_count == 0
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_outbox_async_success_means_durably_accepted_not_remote_completed() -> None:
    definition = _definition(completion_mode=EffectCompletionMode.OUTBOX_ASYNC)
    result = await _service(definition, _EffectRepository(ClaimResult.NEW)).apply(_ctx(), _intent())

    assert isinstance(result.outcome, Success)
    assert result.durably_accepted is True
    assert result.remote_completed is False


@pytest.mark.asyncio
async def test_same_final_key_and_hash_is_noop_success() -> None:
    _RecordingHandler.calls.clear()
    repository = _EffectRepository(ClaimResult.MATCH)
    result = await _service(_definition(), repository).apply(_ctx(), _intent())

    assert isinstance(result.outcome, Success)
    assert result.idempotent_replay is True
    assert _RecordingHandler.calls == []
    [claim] = repository.calls
    assert "test.effect@v1" in claim["idempotency_key"]
    assert "session:31" in claim["idempotency_key"]
    assert "work-item:41" in claim["idempotency_key"]
    assert claim["idempotency_key"].endswith(":operation-1")


@pytest.mark.asyncio
async def test_same_final_key_with_different_hash_fails_closed() -> None:
    conflict = IdempotencyConflict(
        provider_code="RUNTIME",
        operation_kind="system_capability_effect",
        idempotency_key="stable-key",
        existing_request_hash="a" * 64,
        incoming_request_hash="b" * 64,
    )
    result = await _service(_definition(), _EffectRepository(conflict)).apply(_ctx(), _intent("B"))

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_stale_precondition_remains_business_reject_for_plugin_redecision() -> None:
    repository = _StatefulEffectRepository()
    _StaleHandler.calls = 0
    _StaleHandler.stale = True
    service = _service(_definition(_StaleHandler), repository)
    first = await service.apply(_ctx(), _intent())

    _StaleHandler.stale = False
    second = await service.apply(_ctx(), _intent())

    assert isinstance(first.outcome, BusinessReject)
    assert first.outcome.reason_code == "STALE_PRECONDITION"
    assert first.retryable is False
    assert isinstance(second.outcome, Success)
    assert _StaleHandler.calls == 2
    assert repository.status == "SUCCEEDED"
    redetermination = await repository.list_redecision_evidence(_ctx()["db"], session_id=31, work_item_id=41)
    assert len(redetermination) == 1


@pytest.mark.asyncio
async def test_unknown_handler_exception_is_redacted_retryable_unknown() -> None:
    result = await _service(_definition(_FailingHandler), _EffectRepository(ClaimResult.NEW)).apply(_ctx(), _intent())

    assert isinstance(result.outcome, RetryableFailure)
    assert result.outcome.error_code == "UNKNOWN"
    assert "secret" not in result.outcome.message


@pytest.mark.asyncio
async def test_undeclared_effect_capability_is_rejected_before_claim_or_handler() -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    definition = _definition()
    plugin_definition = SimpleNamespace(
        plugin_key="test_plugin",
        contract_version="v1",
        allowed_capabilities=(),
    )
    service = SystemCapabilityEffectService(
        intent_service=SystemCapabilityIntentService(
            definitions={(definition.capability_key, definition.contract_version): definition},
            plugin_definitions={("test_plugin", "v1"): plugin_definition},
            plugin_index_digest="d" * 64,
            effect_repository=repository,
        )
    )

    result = await service.apply(_ctx(), _intent())

    assert isinstance(result.outcome, ContractViolation)
    assert repository.calls == []


@pytest.mark.asyncio
async def test_binding_mismatch_is_rejected_before_claim() -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    intent = _intent().model_copy(update={"binding_snapshot": {"binding_id": 99, "binding_version": 1}})

    result = await _service(_definition(), repository).apply(_ctx(), intent)

    assert isinstance(result.outcome, ContractViolation)
    assert repository.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("creator_authority", "PLUGIN_SELF_ASSERTED"),
        ("authorization_policy", "PLUGIN_SELECTED_POLICY"),
    ],
)
async def test_creator_and_policy_must_match_runtime_owned_identity(field: str, value: str) -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    intent = _intent().model_copy(update={field: value})

    result = await _service(_definition(), repository).apply(_ctx(), intent)

    assert isinstance(result.outcome, ContractViolation)
    assert repository.calls == []


@pytest.mark.asyncio
async def test_locked_binding_row_mismatch_is_rejected_before_claim() -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    ctx = _ctx()
    ctx["plugin_binding"] = SimpleNamespace(
        id=9,
        binding_version=2,
        plugin_key="test_plugin",
        contract_version="v1",
        generated_index_digest="d" * 64,
        is_enabled=True,
        is_revoked=False,
    )

    result = await _service(_definition(), repository).apply(ctx, _intent())

    assert isinstance(result.outcome, ContractViolation)
    assert repository.calls == []


@pytest.mark.asyncio
async def test_plugin_cannot_switch_provider_identity_to_evade_payload_conflict() -> None:
    repository = _StatefulEffectRepository()
    service = _service(_definition(), repository)
    first = await service.apply(_ctx(), _intent("A"))
    switched = _intent("B").model_copy(
        update={"provider_snapshot": {"provider_code": "PLUGIN_SELECTED", "profile": "runtime"}}
    )
    second = await service.apply(_ctx(), switched)

    assert isinstance(first.outcome, Success)
    assert isinstance(second.outcome, ContractViolation)
    assert all(call["provider_code"] == "RUNTIME" for call in repository.calls)


@pytest.mark.asyncio
async def test_same_runtime_identity_and_operation_rejects_different_payload() -> None:
    repository = _StatefulEffectRepository()
    service = _service(_definition(), repository)

    first = await service.apply(_ctx(), _intent("A"))
    second = await service.apply(_ctx(), _intent("B"))

    assert isinstance(first.outcome, Success)
    assert isinstance(second.outcome, ContractViolation)
    assert second.outcome.error_code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_material_unit_mutation_service_participates_in_outer_transaction_only() -> None:
    from src.app.runtime.orchestration.services.material_unit_mutation_service import MaterialUnitMutationService

    class _Repository:
        async def get_by_pkg_code_for_update(self, _db: object, _pkg_code: str) -> None:
            return None

        async def add_and_flush(self, db: object, value: object) -> None:
            db.add(value)  # type: ignore[attr-defined]
            await db.flush()  # type: ignore[attr-defined]

        async def flush(self, db: object) -> None:
            await db.flush()  # type: ignore[attr-defined]

    db = _MutationDb()
    session = SimpleNamespace(id=31, current_material_unit_id=None)
    material_unit = await MaterialUnitMutationService(repository=_Repository()).create(  # type: ignore[arg-type]
        {
            "db": db,
            "session": session,
            "workline": SimpleNamespace(plugin_key=None),
        },
        {
            "pkg_code": "PKG-LOCAL-1",
            "material_identity_key": "MAT-LOCAL-1",
            "six_in_one": {"PkgID": "PKG-LOCAL-1"},
            "status": "IN_TRANSIT",
        },
    )

    assert material_unit.id == session.current_material_unit_id
    assert db.flush_count >= 1
    assert db.commit_count == 0
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_device_command_runtime_entry_writes_command_and_outbox_without_remote_io_or_commit() -> None:
    from src.app.device.models.command import DeviceCommand
    from src.app.device.services.device_command_service import DeviceCommandService
    from src.app.sys.models import SystemOutbox

    db = _MutationDb()
    command, outbox = await DeviceCommandService().prepare_runtime_effect(
        db,  # type: ignore[arg-type]
        request=SimpleNamespace(
            action="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            payload={"business_key": "PKG-1"},
            command_code=None,
        ),
        target_device=SimpleNamespace(id=71, device_code="ARM-71"),
        session=SimpleNamespace(id=31, workline_id=41, plugin_key="rough_sorter", contract_version="rough_sorter.v2"),
        workline=SimpleNamespace(id=41, plugin_key="rough_sorter"),
        idempotency_key="system-capability:device.device_command_write@v1:session:31:work-item:41:pick-1",
        trace_id="trace-device-effect",
    )

    assert isinstance(command, DeviceCommand)
    assert isinstance(outbox, SystemOutbox)
    assert outbox.dispatch_key == f"device-command:{command.command_code}"
    assert getattr(outbox.status, "value", outbox.status) == "NEW"
    assert db.commit_count == 0
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_session_hold_mutation_does_not_create_runtime_hold_or_commit() -> None:
    from src.app.runtime.orchestration.services.session_hold_mutation_service import SessionHoldMutationService

    db = _MutationDb()
    session = SimpleNamespace(
        id=31,
        version=3,
        status="RUNNING",
        current_wait_type=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
        awaiting_device_command_code=None,
        ended_at=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
    )
    await SessionHoldMutationService().hold(
        db,
        session=session,
        failure_domain="ROUGH_SORTER",
        reason_code="DEVICE_TIMEOUT",
        message="等待人工检查",
        fact_version="session:3",
    )

    assert getattr(session.status, "value", session.status) == "MANUAL_HOLD"
    assert session.failure_code == "DEVICE_TIMEOUT"
    assert all(type(value).__name__ != "RuntimeHold" for value in db.added)
    assert db.commit_count == 0
    assert db.rollback_count == 0
