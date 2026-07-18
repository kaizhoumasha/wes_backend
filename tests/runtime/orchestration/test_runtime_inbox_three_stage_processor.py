"""Tests for RuntimeInbox 三阶段 Processor 拆分 services (Task 5).

覆盖:
- RuntimeInboxValidationService: SCAN gate + ESTOP/TIMER 路由
- RuntimeInboxOrchestratorDelegate: pure delegate 透传
- RuntimeInboxWriteBackService: WRITE 锁回调
- RuntimeInboxProcessorBridge (composition): 单条 claim-and-process
"""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from src.app.runtime.orchestration.effect_result import (
    RuntimeIntentEffectResult,
    WriteBackDisposition,
)
from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxManualHoldEvidence
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxService,
    validate_replay_envelope,
)
from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_context_loader as context_loader
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxAttemptRuntime,
    RuntimeInboxProcessorBridge,
    _duplicate_entry_material_conflict,
    _load_related_entities,
    _normalized_entry_material_evidence,
    _project_replay_request,
    _snapshot_inbox_for_diagnostic,
    _write_set_from_recorded_replay,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service import (
    RuntimeInboxOrchestratorDelegate,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxReplaySourceValidation,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
    ValidationOutcome,
    _entry_event_types_for_workline,
    _scan_completed_has_any_barcode_payload,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackService,
    WriteBackState,
    _is_late_or_duplicate_command_result_for_session,
    _record_duplicate_entry_archive_timeline,
    _record_late_command_result_archive_timeline,
    _result_requires_outbox_dispatch,
    _session_write_snapshot,
)
from src.app.workline.constants import WORKLINE_INBOX_PROCESSING_STALE_SECONDS
from src.app.workline.trace_context import TraceContext
from src.utils.timezone import timezone

# ============================================================
# Helpers
# ============================================================


class _DeadlineQueryInput(BaseModel):
    value: int


class _DeadlineQueryOutput(BaseModel):
    value: int


class _ProcessorUncooperativeHandler:
    release: asyncio.Event | None = None

    def __init__(self, _context: object | None = None) -> None:
        pass

    async def __call__(self, _request: _DeadlineQueryInput) -> _DeadlineQueryOutput:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert self.release is not None
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:  # noqa: S112 - 模拟不协作 handler 持续吞取消信号。
                    continue
            return _DeadlineQueryOutput(value=1)


def test_orchestrator_lock_renews_beyond_processing_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """单条处理超过初始 TTL 时，session 锁必须持续续期。"""

    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_processor_service as module

    captured: dict[str, Any] = {}

    class _LockStub:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def acquire(self, lock_key: str, *, db: Any) -> Any:
            return SimpleNamespace(lock_key=lock_key, db=db)

    monkeypatch.setattr(module, "get_redis", lambda: object())
    monkeypatch.setattr(module, "RedisDistributedLock", _LockStub)

    provider = module._build_orchestrator_lock_provider(object())
    _ = provider("session:42")

    assert captured["auto_renewal"] is True
    assert captured["default_ttl"] > module.INBOX_PROCESS_TIMEOUT_SECONDS


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def _project_shape_validated_replay(inbox: Any) -> Any:
    """纯投影测试只提供已完成 shape gate 的 validator 产物。"""

    return _project_replay_request(
        inbox,
        validated_source=RuntimeInboxReplaySourceValidation(
            envelope=validate_replay_envelope(inbox.payload_json),
            root_source=SimpleNamespace(),  # 投影本身不读取 root；DB 真实性由独立 validator 测试覆盖。
        ),
    )


def test_recorded_replay_validates_but_does_not_reexecute_recorded_intents() -> None:
    """Recorded decision 仅恢复 state/evidence，不得再次产生物理 EFFECT。"""
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution

    write_set = _write_set_from_recorded_replay(
        RecordedReplayResolution(
            decision={
                "outcome_code": "ROUTE_A",
                "next_state": {"step": 2},
                "intents": [
                    {
                        "kind": "COMMAND",
                        "action": "MOVE",
                        "idempotency_key": "operation-1",
                        "payload_json": {"target": "A-01"},
                        "result_policy": "FIRE_AND_FORGET",
                    }
                ],
            }
        ),
        fallback_state={},
    )

    assert write_set.intents == ()
    assert write_set.next_state == {"step": 2}


@pytest.mark.parametrize(
    "raw_intents",
    [
        [{"kind": "COMMAND", "payload_json": {"secret": "must-not-leak"}}],
        [{"kind": "CONTINUE_NEXT"}] * 33,
    ],
)
def test_recorded_replay_invalid_or_excess_intents_fail_closed_without_payload(
    raw_intents: list[dict[str, Any]],
) -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution

    write_set = _write_set_from_recorded_replay(
        RecordedReplayResolution(decision={"outcome_code": "ROUTE_A", "next_state": {}, "intents": raw_intents}),
        fallback_state={},
    )

    assert write_set.intents == ()
    assert write_set.outcome_code == "HOLD"
    assert write_set.hold_reason == "RECORDED_REPLAY_RECORD_INVALID"


def test_recorded_replay_restores_a_complete_legal_hold_decision() -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution

    write_set = _write_set_from_recorded_replay(
        RecordedReplayResolution(
            decision={
                "outcome_code": "HOLD",
                "hold_reason": "BUSINESS_RULE_HOLD",
                "next_state": {"step": 2},
                "intents": [],
            }
        ),
        fallback_state={"step": 1},
    )

    assert write_set.outcome_code == "HOLD"
    assert write_set.hold_reason == "BUSINESS_RULE_HOLD"
    assert write_set.next_state == {"step": 2}
    assert write_set.intents == ()


@pytest.mark.parametrize(
    "decision",
    [
        {"outcome_code": "ROUTE_A", "intents": []},
        {"outcome_code": "ROUTE_A", "next_state": [], "intents": []},
        {"next_state": {}, "intents": []},
        {"outcome_code": "", "next_state": {}, "intents": []},
        {"outcome_code": "HOLD", "next_state": {}, "intents": []},
        {"outcome_code": "ROUTE_A", "hold_reason": "BAD_COMBINATION", "next_state": {}, "intents": []},
        {
            "outcome_code": "HOLD",
            "hold_reason": "BUSINESS_RULE_HOLD",
            "next_state": {},
            "intents": [{"kind": "CONTINUE_NEXT"}],
        },
    ],
)
def test_recorded_replay_invalid_decision_fields_fail_closed(decision: dict[str, Any]) -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution

    write_set = _write_set_from_recorded_replay(
        RecordedReplayResolution(decision=decision),
        fallback_state={"step": 1},
    )

    assert write_set.outcome_code == "HOLD"
    assert write_set.hold_reason == "RECORDED_REPLAY_RECORD_INVALID"
    assert write_set.intents == ()


@pytest.mark.asyncio
async def test_attempt_runtime_close_delegates_to_gateway_and_is_idempotent() -> None:
    closes = 0

    class Gateway:
        async def aclose(self) -> None:
            nonlocal closes
            closes += 1

    runtime = RuntimeInboxAttemptRuntime(
        attempt_id="lease-1",
        port_registry=object(),  # type: ignore[arg-type]
        context=object(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
    )
    await runtime.aclose()
    await runtime.aclose()
    assert closes == 1


@pytest.mark.asyncio
async def test_bridge_close_failure_does_not_override_successful_attempt_result() -> None:
    inbox = _make_inbox(kind="EXTERNAL_HTTP", payload_json={"event_type": "CALLBACK_COMPLETED"})
    inbox.event_type = "CALLBACK_COMPLETED"

    class Db:
        async def commit(self) -> None:
            pass

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            return True

    class Runtime:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    bridge = RuntimeInboxProcessorBridge(
        inbox_repository=Repository(),  # type: ignore[arg-type]
        inbox_service=InboxService(),  # type: ignore[arg-type]
    )
    bridge.create_attempt_runtime = lambda *_args, **_kwargs: Runtime()  # type: ignore[method-assign]

    result = await bridge.process_claimed(Db(), claim={"id": inbox.id, "processor_token": "lease-1"})

    assert result["success"] == 1
    assert result["processed"] == 1


@pytest.mark.asyncio
async def test_process_cancellation_closes_attempt_and_leaves_no_background_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = _make_inbox(
        inbox_id=91,
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "BOX-1"}},
    )
    inbox.event_type = "SCAN_COMPLETED"
    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={},
        plugin_identity="plugin@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest="b" * 64,
        status="RUNNING",
        awaiting_device_command_code=None,
    )
    started = asyncio.Event()
    handler_cancelled = asyncio.Event()

    class Db:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class Runtime:
        handler_task: asyncio.Task[None] | None = None

        async def aclose(self) -> None:
            assert self.handler_task is not None
            self.handler_task.cancel()
            await asyncio.gather(self.handler_task, return_exceptions=True)

    runtime = Runtime()

    class Runner:
        async def run(self, _context: object) -> object:
            async def handler() -> None:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    handler_cancelled.set()

            runtime.handler_task = asyncio.create_task(handler())
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return session, SimpleNamespace(id=20), None, None, {}, object(), True

    async def not_duplicate(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._is_duplicate_entry_event",
        not_duplicate,
    )
    bridge = RuntimeInboxProcessorBridge(
        inbox_repository=Repository(),  # type: ignore[arg-type]
        plugin_attempt_runner=Runner(),
    )
    bridge.create_attempt_runtime = lambda *_args, **_kwargs: runtime  # type: ignore[method-assign]
    process_task = asyncio.create_task(bridge.process_claimed(Db(), claim={"id": 91, "processor_token": "lease-1"}))
    await asyncio.wait_for(started.wait(), timeout=1)
    process_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await process_task

    assert handler_cancelled.is_set()
    assert runtime.handler_task is not None and runtime.handler_task.done()


@pytest.mark.asyncio
async def test_platform_processor_returns_within_gateway_hard_deadline() -> None:
    from src.app.runtime.capability_port_registry import CapabilityPortRegistry, RuntimeCapabilityContext
    from src.app.runtime.system_capabilities.definition import (
        EffectCompletionMode,
        SystemCapabilityDefinition,
        SystemCapabilityMode,
    )
    from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway
    from src.app.runtime.system_capabilities.outcomes import RetryableFailure
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition

    release = asyncio.Event()
    _ProcessorUncooperativeHandler.release = release
    definition = SystemCapabilityDefinition(
        capability_key="wms.lookup",
        contract_version="v1",
        mode=SystemCapabilityMode.QUERY,
        input_model=_DeadlineQueryInput,
        output_model=_DeadlineQueryOutput,
        handler_factory=_ProcessorUncooperativeHandler,
        required_ports=(),
        admission="runtime",
        timeout_seconds=0.005,
        completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
        audit_policy="metadata",
    )
    registry = CapabilityPortRegistry()
    context = RuntimeCapabilityContext(registry)
    gateway = SystemCapabilityGateway(
        attempt_id="lease-hard-deadline",
        definitions={("wms.lookup", "v1"): definition},
        allowed_capabilities=frozenset({("wms.lookup", "v1")}),
        context=context,
        admission_profile="runtime",
    )
    runtime = RuntimeInboxAttemptRuntime(
        attempt_id="lease-hard-deadline",
        port_registry=registry,
        context=context,
        gateway=gateway,
    )

    class Runner:
        async def run(self, plugin_context: object) -> AttemptWriteSet:
            result = await plugin_context.runtime.gateway.execute("wms.lookup", "v1", {"value": 1})  # type: ignore[attr-defined]
            assert isinstance(result.outcome, RetryableFailure)
            assert result.outcome.error_code == "TIMEOUT"
            return AttemptWriteSet(evidence=(), next_state={}, intents=(), outcome_code="ROUTE_A")

    class Db:
        async def commit(self) -> None:
            pass

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **_kwargs: object) -> WriteDisposition:
            return WriteDisposition.COMMITTED

    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={},
        plugin_identity="plugin@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest="b" * 64,
    )
    bridge = RuntimeInboxProcessorBridge(
        plugin_attempt_runner=Runner(),
        writeback_service=WriteBack(),  # type: ignore[arg-type]
    )
    try:
        result = await asyncio.wait_for(
            bridge._process_platform_plugin_attempt(
                Db(),
                inbox=_make_inbox(inbox_id=91, payload_json={"event_type": "SCAN_COMPLETED"}),
                session=session,
                workline=SimpleNamespace(id=20),
                resolved_event_type="SCAN_COMPLETED",
                processor_token="lease-hard-deadline",
                attempt_runtime=runtime,
            ),
            timeout=0.1,
        )
        assert result["success"] == 1
        close_report = await gateway.aclose(grace_seconds=0.005)
        assert close_report.unterminated == 1
    finally:
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)


def test_plugin_write_set_bounds_use_canonical_utf8_bytes_at_exact_and_over_one_boundaries() -> None:
    """live/replay 共用同一 write-set 边界，且不会把超限业务值带入 Hold 原因。"""

    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as module
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet

    bounded = getattr(module, "_bounded_plugin_write_set", None)
    assert bounded is not None

    unicode_state = {"nested": {"value": "货"}}
    state_bytes = len(json.dumps(unicode_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    limits = SimpleNamespace(
        max_next_state_bytes=state_bytes,
        max_intent_bytes=128,
        max_intents_total_bytes=256,
        max_write_set_bytes=512,
        max_intents=32,
    )
    exact = bounded(
        AttemptWriteSet(evidence=(), next_state=unicode_state, intents=(), outcome_code="ROUTE_A"),
        limits=limits,
    )
    assert exact.hold_reason is None

    limits.max_next_state_bytes = state_bytes - 1
    rejected = bounded(
        AttemptWriteSet(evidence=(), next_state=unicode_state, intents=(), outcome_code="ROUTE_A"),
        limits=limits,
    )
    assert rejected.intents == ()
    assert rejected.outcome_code == "HOLD"
    assert rejected.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
    assert "货" not in rejected.hold_reason


def test_plugin_write_set_bounds_reject_single_total_count_and_whole_write_set() -> None:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as module
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet

    bounded = getattr(module, "_bounded_plugin_write_set", None)
    assert bounded is not None
    intent = RuntimeIntent.continue_next(payload={"nested": {"value": "货"}})
    intent_bytes = len(
        json.dumps(intent.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    base = {
        "max_next_state_bytes": 128,
        "max_intent_bytes": intent_bytes,
        "max_intents_total_bytes": intent_bytes,
        "max_write_set_bytes": 2048,
        "max_intents": 32,
    }
    accepted = bounded(
        AttemptWriteSet(evidence=(), next_state={}, intents=(intent,), outcome_code="ROUTE_A"),
        limits=SimpleNamespace(**base),
    )
    assert accepted.hold_reason is None

    for override in (
        {"max_intent_bytes": intent_bytes - 1},
        {"max_intents_total_bytes": intent_bytes * 2 - 1},
        {"max_intents": 1},
        {"max_write_set_bytes": 1},
    ):
        limits_dict = base | override
        intents = (intent, intent) if "max_intents_total_bytes" in override or "max_intents" in override else (intent,)
        rejected = bounded(
            AttemptWriteSet(evidence=(), next_state={}, intents=intents, outcome_code="ROUTE_A"),
            limits=SimpleNamespace(**limits_dict),
        )
        assert rejected.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
        assert rejected.intents == ()


def _make_inbox(
    *,
    inbox_id: int = 1,
    kind: str = "DEVICE_EVENT",
    payload_json: dict[str, Any] | None = None,
    session_id: int = 10,
    workline_id: int = 20,
    device_id: int | None = None,
    command_id: int | None = None,
    trace_id: str = "trace-test",
    event_id: str | None = "evt-test",
    causation_id: str | None = None,
    attempt_count: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=inbox_id,
        kind=kind,
        payload_json=payload_json or {"event_type": "SCAN_COMPLETED"},
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
        workline_id=workline_id,
        execution_session_id=session_id,
        device_id=device_id,
        command_id=command_id,
        attempt_count=attempt_count,
    )


@pytest.mark.asyncio
async def test_claim_one_uses_injected_runtime_inbox_repository() -> None:
    """claim 必须服从构造器注入，不能旁路到全局 singleton。"""

    calls: list[dict[str, Any]] = []

    class _InjectedRepository:
        async def claim_received_with_token(self, db: Any, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append({"db": db, **kwargs})
            return [{"id": 71, "processor_token": kwargs["processor_token"]}]

    db = object()
    bridge = RuntimeInboxProcessorBridge(inbox_repository=_InjectedRepository())  # type: ignore[arg-type]

    claim = await bridge._claim_one(db, processor_token="injected-token")  # type: ignore[arg-type]

    assert claim == {"id": 71, "processor_token": "injected-token"}
    assert calls == [
        {
            "db": db,
            "limit": 1,
            "processor_token": "injected-token",
            "stale_after_seconds": WORKLINE_INBOX_PROCESSING_STALE_SECONDS,
        }
    ]


def _make_session(
    *,
    session_id: int = 10,
    status: str = "RUNNING",
    workline_id: int = 20,
    awaiting_device_command_code: str | None = None,
    current_wait_type: str | None = None,
    context_json: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        workline_id=workline_id,
        status=status,
        awaiting_device_command_code=awaiting_device_command_code,
        current_wait_type=current_wait_type,
        context_json=context_json or {},
    )


def _make_workline(workline_id: int = 20) -> SimpleNamespace:
    return SimpleNamespace(id=workline_id, plugin_key="default")


# ============================================================
# Stage 1: Validation service
# ============================================================


class TestScanCompletedGate:
    @pytest.mark.asyncio
    async def test_scan_with_barcode_passes(self) -> None:
        """SCAN_COMPLETED + barcode → 继续走 orchestrator."""
        inbox = _make_inbox(
            payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "ABC"}},
        )
        outcome = await RuntimeInboxValidationService().pre_gate(
            _EmptyDb(),
            inbox=inbox,
            resolved_event_type="SCAN_COMPLETED",
            workline=None,
        )
        assert outcome.proceed_to_orchestrator is True

    @pytest.mark.asyncio
    async def test_scan_without_barcode_fails(self) -> None:
        """SCAN_COMPLETED 缺条码 → FAILED."""
        inbox = _make_inbox(payload_json={"event_type": "SCAN_COMPLETED", "data": {}})
        outcome = await RuntimeInboxValidationService().pre_gate(
            _EmptyDb(),
            inbox=inbox,
            resolved_event_type="SCAN_COMPLETED",
            workline=None,
        )
        assert outcome.proceed_to_orchestrator is False
        assert outcome.error_code is not None
        assert outcome.error_code.value == "CALLBACK_SCHEMA_INVALID"
        assert "barcode" in (outcome.error_message or "").lower() or "条码" in (outcome.error_message or "")

    @pytest.mark.asyncio
    async def test_non_scan_event_passes(self) -> None:
        """非 SCAN_COMPLETED 事件 → 直接通过 (由 ESTOP/TIMER 路由或 orchestrator 判定)."""
        inbox = _make_inbox(payload_json={"event_type": "COMMAND_RESULT", "data": {}})
        outcome = await RuntimeInboxValidationService().pre_gate(
            _EmptyDb(),
            inbox=inbox,
            resolved_event_type="COMMAND_RESULT",
            workline=None,
        )
        assert outcome.proceed_to_orchestrator is True


class _EmptyDb:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


class TestRelatedEntitiesContract:
    @pytest.mark.parametrize(("session_id", "expected"), ((41, 41), (None, None)))
    def test_diagnostic_snapshot_uses_only_canonical_workline_session_id(
        self,
        session_id: int | None,
        expected: int | None,
    ) -> None:
        data = {"session_id": session_id} if session_id is not None else {}
        inbox = RuntimeInbox(
            id=1,
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            kind="INTERNAL_EVENT",
            payload_json={"event_type": "INTERNAL_EVENT", "data": data},
            execution_session_id=999,
        )

        snapshot = _snapshot_inbox_for_diagnostic(inbox)
        trace = TraceContext.from_runtime(inbox=snapshot)

        assert snapshot.session_id == expected
        assert trace.session_id == expected

    @pytest.mark.parametrize("kind", ("INTERNAL_EVENT", "TIMER_TIMEOUT"))
    def test_workline_session_id_uses_explicit_column_with_canonical_consistency(self, kind: str) -> None:
        inbox = RuntimeInbox(
            provider_code="TEST",
            event_type=kind,
            kind=kind,
            payload_json={"event_type": kind, "data": {"session_id": 41}},
            workline_session_id=41,
            execution_session_id=999,
        )

        assert context_loader._canonical_workline_session_id(inbox) == 41

    def test_workline_session_id_rejects_explicit_canonical_mismatch(self) -> None:
        inbox = RuntimeInbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            kind="INTERNAL_EVENT",
            payload_json={"data": {"session_id": 41}},
            workline_session_id=42,
        )

        with pytest.raises(ValueError, match="workline_session_id mismatch"):
            context_loader._canonical_workline_session_id(inbox)

    def test_execution_session_id_is_not_a_workline_session_fallback(self) -> None:
        inbox = RuntimeInbox(
            provider_code="TEST",
            event_type="INTERNAL_EVENT",
            kind="INTERNAL_EVENT",
            payload_json={"data": {}},
            execution_session_id=999,
        )

        assert context_loader._canonical_workline_session_id(inbox) is None

    def test_source_device_does_not_read_dynamic_normalized_input(self) -> None:
        device = SimpleNamespace(device_code="DYNAMIC-ONLY")
        inbox = SimpleNamespace(payload_json={}, normalized_input=SimpleNamespace(device_code="DYNAMIC-ONLY"))

        resolved = context_loader._resolve_effect_source_device(
            inbox, SimpleNamespace(context_json={}), {"R": [device]}
        )

        assert resolved is None

    @pytest.mark.asyncio
    async def test_wrapper_returns_device_before_command_with_distinct_sentinels(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RuntimeInbox context loader 合同固定为 session/workline/device/command。"""

        class _DeviceSentinel:
            pass

        class _CommandSentinel:
            pass

        device = _DeviceSentinel()
        command = _CommandSentinel()

        async def runtime_loader(*args: object, **kwargs: object) -> dict[str, object]:
            _ = args, kwargs
            return {
                "session": "session",
                "workline": "workline",
                "device": device,
                "command": command,
                "devices_by_role": {},
                "services": "services",
                "safety_checked": True,
            }

        monkeypatch.setattr(
            "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_context_loader.load_related_entities",
            runtime_loader,
        )

        loaded = await _load_related_entities(SimpleNamespace(), SimpleNamespace(id=1))

        assert loaded[2] is device
        assert loaded[3] is command


def test_processor_default_writeback_uses_injected_runtime_inbox_service() -> None:
    """bridge 与默认 write-back 必须共享同一 fenced terminal service。"""
    inbox_service = SimpleNamespace()

    processor = RuntimeInboxProcessorBridge(inbox_service=inbox_service)

    assert processor._writeback_service.inbox_service is inbox_service


def test_processor_creates_fresh_attempt_runtime_for_every_claim() -> None:
    """Gateway、Port proxy 与 evidence cache 不得跨 attempt 复用。"""
    processor = RuntimeInboxProcessorBridge()

    first = processor.create_attempt_runtime("lease-1")
    second = processor.create_attempt_runtime("lease-2")

    assert first is not second
    assert first.attempt_id == "lease-1"
    assert second.attempt_id == "lease-2"
    assert first.gateway is not second.gateway
    assert first.port_registry is not second.port_registry


@pytest.mark.asyncio
@pytest.mark.parametrize("use_recorded_replay", [False, True])
async def test_binding_never_falls_back_to_legacy_when_generated_request_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    use_recorded_replay: bool,
) -> None:
    """平台 binding 请求不完整时 fail closed，禁止回落 legacy Orchestrator。"""

    events: list[str] = []
    original_payload = {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "BOX-1"}}
    replay_envelope = _replay_envelope(original_kind="DEVICE_EVENT", original_payload=original_payload)
    inbox = _make_inbox(
        inbox_id=91,
        kind="REPLAY_REQUEST" if use_recorded_replay else "DEVICE_EVENT",
        payload_json=replay_envelope if use_recorded_replay else original_payload,
    )
    inbox.event_type = "REPLAY_REQUEST" if use_recorded_replay else "SCAN_COMPLETED"
    session = SimpleNamespace(
        id=10,
        plugin_binding_id=17,
        status="RUNNING",
        awaiting_device_command_code=None,
    )
    workline = SimpleNamespace(id=20)

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class InboxService:
        async def mark_failed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("platform-fail-closed")
            return True

    class Validator:
        async def validate_for_consumption(self, *_args: object, **_kwargs: object) -> object:
            return RuntimeInboxReplaySourceValidation(envelope=replay_envelope, root_source=SimpleNamespace(id=7))

    class ReplayService:
        async def load(self, *_args: object, **_kwargs: object) -> object:
            events.append("MUST_NOT_RECORDED_REPLAY")
            raise AssertionError("recorded replay seam also requires explicit platform runner admission")

    class Processor:
        async def process(self, *_args: object, **kwargs: object) -> OrchestratorResult:
            events.append("legacy-orchestrator")
            await kwargs["write_callback"](OrchestratorResult(success=True, intents=[]))
            return OrchestratorResult(success=True, intents=[])

    class WriteBack:
        def build_write_callback(self, db: object, **kwargs: object) -> object:
            async def callback(_result: object) -> None:
                state = kwargs["state"]
                state.write_effects_applied = True
                state.disposition = WriteBackDisposition.PROCESSED
                await db.commit()  # type: ignore[attr-defined]

            return callback

        async def commit_plugin_attempt(self, *_args: object, **_kwargs: object) -> object:
            events.append("MUST_NOT_PLATFORM")
            raise AssertionError("default bridge must keep legacy semantics")

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return session, workline, None, None, {}, object(), True

    async def not_duplicate(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._is_duplicate_entry_event",
        not_duplicate,
    )

    result = await RuntimeInboxProcessorBridge(
        inbox_repository=Repository(),  # type: ignore[arg-type]
        inbox_service=InboxService(),  # type: ignore[arg-type]
        processor_service=Processor(),  # type: ignore[arg-type]
        writeback_service=WriteBack(),  # type: ignore[arg-type]
        replay_source_validator=Validator(),  # type: ignore[arg-type]
        recorded_replay_service=ReplayService(),  # type: ignore[arg-type]
    ).process_claimed(Db(), claim={"id": 91, "processor_token": "lease-1"})

    assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}
    assert "legacy-orchestrator" not in events
    assert "MUST_NOT_RECORDED_REPLAY" not in events
    assert "platform-fail-closed" in events


@pytest.mark.asyncio
async def test_platform_plugin_claim_runs_three_stages_without_db_in_query_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition

    events: list[str] = []
    inbox = _make_inbox(
        inbox_id=91,
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "BOX-1"}},
    )
    inbox.event_type = "SCAN_COMPLETED"
    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={"step": 1},
        plugin_identity="plugin.rough-sorter@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest="b" * 64,
        status="RUNNING",
        awaiting_device_command_code=None,
    )
    workline = SimpleNamespace(id=20)

    class Db:
        in_transaction = True

        async def commit(self) -> None:
            events.append("claim-snapshot-commit-release")
            self.in_transaction = False

        async def rollback(self) -> None:
            events.append("rollback")
            self.in_transaction = False

    db = Db()

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class Runner:
        async def run(self, context: object) -> AttemptWriteSet:
            assert db.in_transaction is False
            assert not hasattr(context, "db")
            assert not hasattr(context, "session")
            assert not hasattr(context, "repository")
            events.append("query-decision")
            return AttemptWriteSet(
                evidence=("evidence",),
                next_state={"step": 2},
                intents=(),
                outcome_code="ROUTE_A",
            )

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **kwargs: object) -> WriteDisposition:
            assert kwargs["inbox_id"] == 91
            assert kwargs["session_id"] == 10
            events.append("lock-revalidate-write")
            return WriteDisposition.COMMITTED

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return session, workline, None, None, {}, object(), True

    async def not_duplicate(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._is_duplicate_entry_event",
        not_duplicate,
    )

    result = await RuntimeInboxProcessorBridge(
        inbox_repository=Repository(),  # type: ignore[arg-type]
        writeback_service=WriteBack(),  # type: ignore[arg-type]
        plugin_attempt_runner=Runner(),
    ).process_claimed(db, claim={"id": 91, "processor_token": "lease-1"})

    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    assert events == ["claim-snapshot-commit-release", "query-decision", "lock-revalidate-write"]


@pytest.mark.parametrize("kind", ["INTERNAL_EVENT", "EXTERNAL_HTTP"])
@pytest.mark.parametrize("correlation", ["success", "missing", "mismatch"])
@pytest.mark.asyncio
async def test_pick_result_callback_guards_dispatcher_before_plugin_writes(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    correlation: str,
) -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition

    calls = {"archive": 0, "runner": 0, "writeback": 0, "terminal": 0}
    callback_code = {"success": "CMD-1", "missing": None, "mismatch": "CMD-OTHER"}[correlation]
    payload = {"logical_route": "PICK_AND_PUT_RESULT", "result": "SUCCESS"}
    if callback_code is not None:
        payload["command_code"] = callback_code
    inbox = _make_inbox(inbox_id=91, kind=kind, payload_json=payload)
    inbox.event_type = "PICK_AND_PUT_RESULT" if kind == "INTERNAL_EVENT" else "EXTERNAL_HTTP"
    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={"step": 1},
        plugin_identity="plugin.rough-sorter@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_config_hash="c" * 64,
        plugin_index_digest="b" * 64,
        status="WAITING_DEVICE_RESULT",
        awaiting_device_command_code="CMD-1",
    )
    command = None if callback_code is None else SimpleNamespace(command_code=callback_code, status="PENDING")

    class Db:
        in_transaction = True

        async def commit(self) -> None:
            self.in_transaction = False

        async def rollback(self) -> None:
            self.in_transaction = False

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            calls["terminal"] += 1
            return True

    class Runner:
        async def run(self, _context: object) -> AttemptWriteSet:
            calls["runner"] += 1
            return AttemptWriteSet(evidence=(), next_state={}, intents=(), outcome_code="ROUTE_A")

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **_kwargs: object) -> WriteDisposition:
            calls["writeback"] += 1
            return WriteDisposition.COMMITTED

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return session, SimpleNamespace(id=20), None, command, {}, object(), True

    async def not_duplicate(*_args: object, **_kwargs: object) -> bool:
        return False

    async def record_archive(*_args: object, **_kwargs: object) -> None:
        calls["archive"] += 1

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._is_duplicate_entry_event",
        not_duplicate,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._record_late_command_result_archive_timeline",
        record_archive,
    )

    result = await RuntimeInboxProcessorBridge(
        inbox_repository=Repository(),  # type: ignore[arg-type]
        inbox_service=InboxService(),  # type: ignore[arg-type]
        writeback_service=WriteBack(),  # type: ignore[arg-type]
        plugin_attempt_runner=Runner(),
    ).process_claimed(Db(), claim={"id": 91, "processor_token": "lease-1"})

    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    if correlation == "success":
        assert calls == {"archive": 0, "runner": 1, "writeback": 1, "terminal": 0}
    else:
        assert calls == {"archive": 1, "runner": 0, "writeback": 0, "terminal": 1}


@pytest.mark.parametrize(
    ("capability_key", "expected_artifact"),
    [
        ("material_flow.material_unit_write", "material"),
        ("device.device_command_write", "device+outbox"),
        ("runtime.session_hold", "session-hold"),
    ],
)
@pytest.mark.asyncio
async def test_platform_process_claimed_runs_effect_before_state_and_terminal(
    monkeypatch: pytest.MonkeyPatch,
    capability_key: str,
    expected_artifact: str,
) -> None:
    from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
    from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier
    from src.app.runtime.orchestration.services.idempotency_guard import ClaimResult
    from src.app.runtime.system_capabilities.definition import EffectCompletionMode
    from src.app.runtime.system_capabilities.outcomes import Success
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet

    events: list[str] = []
    inbox = _make_inbox(
        inbox_id=91,
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "BOX-1"}},
    )
    inbox.event_type = "SCAN_COMPLETED"
    inbox.processor_token = "lease-1"
    inbox.execution_session_id = 71
    inbox.correlation_id = "corr-1"
    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={},
        plugin_identity="plugin.rough-sorter@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_config_hash="c" * 64,
        plugin_index_digest="b" * 64,
        status="WAITING_DEVICE_RESULT",
        current_material_unit_id=None,
        awaiting_device_command_code=None,
    )
    common = {
        "contract_version": "v1",
        "operation_key": f"inbox:91:{capability_key}",
        "timeout_seconds": 5,
        "creator_authority": "WORKLINE_PLUGIN",
        "authorization_policy": "PLUGIN_DECLARED_CAPABILITY",
        "binding_snapshot": {"binding_id": 17, "binding_version": 4},
        "provider_snapshot": {"provider_code": "RUNTIME", "profile": "runtime"},
    }
    if capability_key == "material_flow.material_unit_write":
        intent = RuntimeIntent.system_capability(
            capability_key=capability_key,
            payload={
                "operation": "CREATE",
                "pkg_code": "PKG-1",
                "material_identity_key": "MAT-1",
                "six_in_one": {},
                "status": "IN_TRANSIT",
            },
            precondition={"expected_absent": True},
            fact_version=0,
            **common,
        )
    elif capability_key == "device.device_command_write":
        intent = RuntimeIntent.system_capability(
            capability_key=capability_key,
            payload={"target_device_id": 31, "action": "PICK_AND_PUT", "payload": {}, "timeout_ms": 30000},
            precondition={"expected_available": True},
            fact_version="device:v1",
            **common,
        )
    else:
        intent = RuntimeIntent.system_capability(
            capability_key=capability_key,
            payload={"failure_domain": "WORKLINE", "reason_code": "WMS_REJECTED", "message": "hold"},
            precondition={"expected_status": "WAITING_DEVICE_RESULT"},
            fact_version="session:7",
            **common,
        )

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class InboxRepository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class PluginRepository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            events.append("lock")
            return SimpleNamespace(
                inbox=inbox,
                session=session,
                work_item=SimpleNamespace(id=51),
                plugin_binding=SimpleNamespace(id=17),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("state+timeline")

    class IntentRepository:
        def prepare_attempt_intents(self, **_kwargs: object) -> tuple[object, ...]:
            return (SimpleNamespace(model=object(), claim={"idempotency_key": "intent-1"}),)

        def add_prepared(self, *_args: object) -> None:
            events.append("intent-ledger")

    class Guard:
        async def claim_or_match(self, *_args: object, **_kwargs: object) -> ClaimResult:
            return ClaimResult.NEW

    class CapabilityEffects:
        async def apply(self, ctx: dict[str, object], _intent: RuntimeIntent) -> object:
            if capability_key == "runtime.session_hold":
                assert ctx["session"].status == "WAITING_DEVICE_RESULT"  # type: ignore[union-attr]
                assert ctx["session"].version == 7  # type: ignore[union-attr]
                ctx["session"].status = "MANUAL_HOLD"  # type: ignore[union-attr]
            events.append(expected_artifact)
            return SimpleNamespace(
                outcome=Success(payload={"accepted": True}),
                completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            )

    class Runner:
        async def run(self, _context: object) -> AttemptWriteSet:
            return AttemptWriteSet(evidence=(), next_state={"step": 2}, intents=(intent,), outcome_code="ROUTE_A")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("terminal")
            return True

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return session, SimpleNamespace(id=20), None, None, {}, object(), True

    async def not_duplicate(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._is_duplicate_entry_event",
        not_duplicate,
    )
    writeback = RuntimeInboxWriteBackService(
        plugin_attempt_repository=PluginRepository(),
        intent_log_repository=IntentRepository(),
        idempotency_guard=Guard(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
        effect_applier=RuntimeIntentEffectApplier(system_capability_effect_service=CapabilityEffects()),
    )

    result = await RuntimeInboxProcessorBridge(
        inbox_repository=InboxRepository(),  # type: ignore[arg-type]
        inbox_service=InboxService(),  # type: ignore[arg-type]
        writeback_service=writeback,
        plugin_attempt_runner=Runner(),
    ).process_claimed(Db(), claim={"id": 91, "processor_token": "lease-1"})

    assert result["success"] == 1
    assert events == ["commit", "lock", "intent-ledger", expected_artifact, "state+timeline", "terminal", "commit"]


@pytest.mark.asyncio
async def test_platform_query_stage_releases_real_async_session_transaction() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    class Runner:
        def __init__(self) -> None:
            self.db: object | None = None

        async def run(self, context: object) -> AttemptWriteSet:
            assert self.db is not None
            assert self.db.in_transaction() is False  # type: ignore[union-attr]
            assert not hasattr(context, "db")
            return AttemptWriteSet(evidence=(), next_state={"step": 2}, intents=(), outcome_code="ROUTE_A")

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **_kwargs: object) -> WriteDisposition:
            return WriteDisposition.COMMITTED

    runner = Runner()
    bridge = RuntimeInboxProcessorBridge(
        plugin_attempt_runner=runner,
        writeback_service=WriteBack(),  # type: ignore[arg-type]
    )
    inbox = _make_inbox(inbox_id=91, payload_json={"event_type": "SCAN_COMPLETED"})
    inbox.event_type = "SCAN_COMPLETED"
    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={"step": 1},
        plugin_identity="plugin@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest="b" * 64,
    )
    try:
        async with session_factory() as db:
            runner.db = db
            await db.begin()
            await db.execute(text("SELECT 1"))
            assert db.in_transaction() is True
            result = await bridge._process_platform_plugin_attempt(
                db,
                inbox=inbox,
                session=session,
                workline=SimpleNamespace(id=20),
                resolved_event_type="SCAN_COMPLETED",
                processor_token="lease-1",
                attempt_runtime=bridge.create_attempt_runtime("lease-1"),
            )
            assert result["success"] == 1
            assert db.in_transaction() is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_platform_stage_one_loads_material_fact_through_repository() -> None:
    from src.app.runtime.orchestration.repositories.material_unit_repository import MaterialUnitFactSnapshot
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition

    class Db:
        async def get(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("Stage1 service must not access MaterialUnit through db.get")

        async def commit(self) -> None:
            return None

    class MaterialRepository:
        async def get_fact_snapshot(self, _db: object, material_unit_id: int) -> MaterialUnitFactSnapshot:
            assert material_unit_id == 31
            return MaterialUnitFactSnapshot(material_unit_id=31, fact_version=73)

    class Runner:
        async def run(self, context: object) -> AttemptWriteSet:
            assert context.snapshot.material_unit_id == 31  # type: ignore[union-attr]
            assert context.snapshot.material_unit_version == 73  # type: ignore[union-attr]
            return AttemptWriteSet(evidence=(), next_state={}, intents=(), outcome_code="ROUTE_A")

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **_kwargs: object) -> WriteDisposition:
            return WriteDisposition.COMMITTED

    bridge = RuntimeInboxProcessorBridge(
        plugin_attempt_runner=Runner(),
        writeback_service=WriteBack(),  # type: ignore[arg-type]
        material_unit_repository=MaterialRepository(),  # type: ignore[arg-type]
    )
    inbox = _make_inbox(inbox_id=91, payload_json={"event_type": "SCAN_COMPLETED"})
    inbox.event_type = "SCAN_COMPLETED"
    result = await bridge._process_platform_plugin_attempt(
        Db(),  # type: ignore[arg-type]
        inbox=inbox,
        session=SimpleNamespace(
            id=10,
            version=7,
            plugin_state_version=3,
            plugin_state_json={},
            plugin_binding_id=17,
            current_material_unit_id=31,
            status="RUNNING",
        ),
        workline=SimpleNamespace(id=20),
        resolved_event_type="SCAN_COMPLETED",
        processor_token="lease-1",
        attempt_runtime=bridge.create_attempt_runtime("lease-1"),
    )

    assert result["success"] == 1


@pytest.mark.asyncio
async def test_platform_safe_retry_requeues_inbox_with_same_lease() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet, WriteDisposition

    calls: list[tuple[str, object]] = []

    class Db:
        async def commit(self) -> None:
            calls.append(("commit", None))

    class Runner:
        async def run(self, _context: object) -> AttemptWriteSet:
            return AttemptWriteSet(evidence=(), next_state={}, intents=(), outcome_code="ROUTE_A")

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **_kwargs: object) -> WriteDisposition:
            return WriteDisposition.SAFE_RETRY

    class InboxService:
        async def mark_failed(self, _db: object, **kwargs: object) -> bool:
            calls.append(("park", kwargs))
            return True

    bridge = RuntimeInboxProcessorBridge(
        plugin_attempt_runner=Runner(),
        writeback_service=WriteBack(),  # type: ignore[arg-type]
        inbox_service=InboxService(),  # type: ignore[arg-type]
    )
    inbox = _make_inbox(inbox_id=91)
    inbox.event_type = "SCAN_COMPLETED"
    result = await bridge._process_platform_plugin_attempt(
        Db(),  # type: ignore[arg-type]
        inbox=inbox,
        session=SimpleNamespace(
            id=10,
            version=7,
            plugin_state_version=3,
            plugin_state_json={},
            plugin_binding_id=17,
            status="RUNNING",
        ),
        workline=SimpleNamespace(id=20),
        resolved_event_type="SCAN_COMPLETED",
        processor_token="lease-1",
        attempt_runtime=bridge.create_attempt_runtime("lease-1"),
    )

    assert result == {"processed": 1, "success": 0, "failed": 0, "skipped": 0, "resource_wait": 1}
    assert calls == [
        ("commit", None),
        (
            "park",
            {
                "inbox_id": 91,
                "lease_token": "lease-1",
                "error_code": "PLUGIN_SNAPSHOT_STALE",
                "error_message": "PLUGIN_SNAPSHOT_STALE",
                "retryable": True,
                "consume_attempt": False,
            },
        ),
        ("commit", None),
    ]


def _replay_envelope(*, original_kind: str, original_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": "req-1",
        "actor": "42",
        "reason": "retry",
        "immediate_source_inbox_id": 8,
        "root_source_inbox_id": 7,
        "original_kind": original_kind,
        "original_payload": original_payload,
        "original_provider_code": "WMS",
        "original_event_type": original_payload.get("event_type", original_kind),
        "original_source_event_id": "source-7",
        "original_payload_hash": "hash-7",
        "original_workline_id": 20,
        "original_device_id": 21,
        "original_command_id": 22,
        "original_workline_session_id": 10,
        "original_execution_session_id": 11,
        "original_correlation_id": "corr-7",
        "original_trace_id": "trace-7",
        "original_event_id": "event-7",
        "original_causation_id": "cause-7",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replay_case",
    ["valid", "legal_hold", "missing_pin", "invalid_intent", "invalid_next_state", "oversize_state"],
)
async def test_platform_recorded_replay_bypasses_runner_and_persists_hold_when_pin_missing(
    monkeypatch: pytest.MonkeyPatch,
    replay_case: str,
) -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution
    from src.app.runtime.workline_plugins.attempt_coordinator import PluginWriteSetLimits, WriteDisposition

    envelope = _replay_envelope(
        original_kind="DEVICE_EVENT",
        original_payload={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "BOX-1"}},
    )
    inbox = _make_inbox(inbox_id=91, kind="REPLAY_REQUEST", payload_json=envelope)
    inbox.event_type = "REPLAY_REQUEST"
    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={"step": 1},
        plugin_identity=None if replay_case == "missing_pin" else "plugin.rough-sorter@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest="b" * 64,
        status="RUNNING",
        awaiting_device_command_code=None,
    )
    workline = SimpleNamespace(id=20)
    runner_calls = 0
    captured_write_set: object | None = None

    class Db:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class Validator:
        async def validate_for_consumption(self, *_args: object, **_kwargs: object) -> object:
            return RuntimeInboxReplaySourceValidation(envelope=envelope, root_source=SimpleNamespace(id=7))

    class Runner:
        async def run(self, _context: object) -> object:
            nonlocal runner_calls
            runner_calls += 1
            raise AssertionError("recorded replay must not invoke live runner")

    class ReplayService:
        async def load(self, _db: object, **kwargs: object) -> RecordedReplayResolution:
            assert kwargs["source_inbox_id"] == 7
            if replay_case == "invalid_intent":
                intents = [{"kind": "COMMAND", "payload_json": {"secret": "must-not-leak"}}]
            elif replay_case == "invalid_next_state":
                intents = [{"kind": "CONTINUE_NEXT"}]
            else:
                intents = []
            outcome_code = "HOLD" if replay_case == "legal_hold" else "ROUTE_A"
            return RecordedReplayResolution(
                evidence=(),
                decision={
                    "outcome_code": outcome_code,
                    "hold_reason": "BUSINESS_RULE_HOLD" if replay_case == "legal_hold" else None,
                    "intents": intents,
                    "next_state": [] if replay_case == "invalid_next_state" else {"step": 2},
                },
            )

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **kwargs: object) -> WriteDisposition:
            nonlocal captured_write_set
            captured_write_set = kwargs["write_set"]
            return WriteDisposition.COMMITTED

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return session, workline, None, None, {}, object(), True

    async def not_duplicate(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._is_duplicate_entry_event",
        not_duplicate,
    )
    result = await RuntimeInboxProcessorBridge(
        inbox_repository=Repository(),  # type: ignore[arg-type]
        writeback_service=WriteBack(),  # type: ignore[arg-type]
        replay_source_validator=Validator(),  # type: ignore[arg-type]
        plugin_attempt_runner=Runner(),  # type: ignore[arg-type]
        recorded_replay_service=ReplayService(),  # type: ignore[arg-type]
        plugin_write_set_limits=(
            PluginWriteSetLimits(max_next_state_bytes=1) if replay_case == "oversize_state" else None
        ),
    ).process_claimed(Db(), claim={"id": 91, "processor_token": "lease-1"})

    assert runner_calls == 0
    assert captured_write_set is not None
    if replay_case == "missing_pin":
        assert captured_write_set.hold_reason == "RECORDED_REPLAY_PIN_MISSING"  # type: ignore[union-attr]
        assert result["failed"] == 1
    elif replay_case in {"invalid_intent", "invalid_next_state"}:
        assert captured_write_set.hold_reason == "RECORDED_REPLAY_RECORD_INVALID"  # type: ignore[union-attr]
        assert captured_write_set.intents == ()  # type: ignore[union-attr]
        assert result["failed"] == 1
    elif replay_case == "legal_hold":
        assert captured_write_set.hold_reason == "BUSINESS_RULE_HOLD"  # type: ignore[union-attr]
        assert captured_write_set.intents == ()  # type: ignore[union-attr]
        assert result["failed"] == 1
    elif replay_case == "oversize_state":
        assert captured_write_set.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"  # type: ignore[union-attr]
        assert captured_write_set.next_state == {}  # type: ignore[union-attr]
        assert result["failed"] == 1
    else:
        assert captured_write_set.outcome_code == "ROUTE_A"  # type: ignore[union-attr]
        assert result["success"] == 1


@pytest.mark.parametrize(
    ("original_kind", "original_payload"),
    [
        ("COMMAND_RESULT", {"command_code": "CMD-OLD", "result": "SUCCESS"}),
        ("TIMER_TIMEOUT", {"command_code": "CMD-OLD", "wait_type": "COMMAND_RESULT"}),
    ],
)
@pytest.mark.asyncio
async def test_platform_recorded_replay_precedes_late_callback_and_timer_routes(
    monkeypatch: pytest.MonkeyPatch,
    original_kind: str,
    original_payload: dict[str, Any],
) -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution
    from src.app.runtime.workline_plugins.attempt_coordinator import WriteDisposition

    envelope = _replay_envelope(original_kind=original_kind, original_payload=original_payload)
    inbox = _make_inbox(inbox_id=91, kind="REPLAY_REQUEST", payload_json=envelope)
    inbox.event_type = "REPLAY_REQUEST"
    calls = {"replay": 0, "runner": 0, "writeback": 0}

    class Db:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return inbox

    class Validator:
        async def validate_for_consumption(self, *_args: object, **_kwargs: object) -> object:
            return RuntimeInboxReplaySourceValidation(envelope=envelope, root_source=SimpleNamespace(id=7))

    class ReplayService:
        async def load(self, *_args: object, **_kwargs: object) -> RecordedReplayResolution:
            calls["replay"] += 1
            return RecordedReplayResolution(
                evidence=(),
                decision={"outcome_code": "ROUTE_A", "hold_reason": None, "intents": [], "next_state": {}},
            )

    class Runner:
        async def run(self, _context: object) -> object:
            calls["runner"] += 1
            raise AssertionError("manual replay must not invoke provider/handler")

    class WriteBack:
        async def commit_plugin_attempt(self, *_args: object, **_kwargs: object) -> WriteDisposition:
            calls["writeback"] += 1
            return WriteDisposition.COMMITTED

    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={},
        plugin_identity="plugin.rough-sorter@v1:" + "a" * 64,
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest="b" * 64,
        status="WAITING_DEVICE_RESULT",
        awaiting_device_command_code="CMD-NEW",
    )

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        command = SimpleNamespace(command_code="CMD-OLD", status="COMPLETED")
        return session, SimpleNamespace(id=20), None, command, {}, object(), True

    async def forbidden_route(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("recorded replay must precede timer/late callback gates")

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._handle_timer_timeout",
        forbidden_route,
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._record_late_command_result_archive_timeline",
        forbidden_route,
    )

    result = await RuntimeInboxProcessorBridge(
        inbox_repository=Repository(),  # type: ignore[arg-type]
        writeback_service=WriteBack(),  # type: ignore[arg-type]
        replay_source_validator=Validator(),  # type: ignore[arg-type]
        recorded_replay_service=ReplayService(),  # type: ignore[arg-type]
        plugin_attempt_runner=Runner(),  # type: ignore[arg-type]
    ).process_claimed(Db(), claim={"id": 91, "processor_token": "lease-1"})

    assert result["success"] == 1
    assert calls == {"replay": 1, "runner": 0, "writeback": 1}


@pytest.mark.parametrize(
    ("original_kind", "original_payload"),
    [
        ("COMMAND_RESULT", {"event_type": "COMMAND_RESULT", "command_code": "CMD-1"}),
        ("DEVICE_EVENT", {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "A"}}),
        ("EXTERNAL_HTTP", {"event_type": "WMS_EXCHANGE_COMPLETED"}),
        ("INTERNAL_EVENT", {"event_type": "SESSION_RESUME", "data": {"session_id": 10}}),
        ("TIMER_TIMEOUT", {"event_type": "TIMER_TIMEOUT", "data": {"session_id": 10}}),
    ],
)
def test_replay_request_projects_one_layer_to_original_semantics(
    original_kind: str,
    original_payload: dict[str, Any],
) -> None:
    inbox = _make_inbox(
        inbox_id=9,
        kind="REPLAY_REQUEST",
        payload_json=_replay_envelope(original_kind=original_kind, original_payload=original_payload),
    )

    projected = _project_shape_validated_replay(inbox)

    assert projected.id == 9
    assert projected.kind == original_kind
    assert projected.payload_json == original_payload
    assert projected.workline_id == 20
    assert projected.device_id == 21
    assert projected.command_id == 22
    assert projected.workline_session_id == 10
    assert projected.trace_id == "trace-7"
    assert inbox.kind == "REPLAY_REQUEST"


def test_replay_request_projection_deep_copies_nested_original_payload() -> None:
    original_payload = {"event_type": "INTERNAL_EVENT", "data": {"nested": {"value": "source"}}}
    inbox = _make_inbox(
        kind="REPLAY_REQUEST",
        payload_json=_replay_envelope(original_kind="INTERNAL_EVENT", original_payload=original_payload),
    )

    projected = _project_shape_validated_replay(inbox)
    projected.payload_json["data"]["nested"]["value"] = "mutated"

    assert inbox.payload_json["original_payload"]["data"]["nested"]["value"] == "source"


def test_replay_request_projection_exposes_read_only_identity_outside_original_payload() -> None:
    original_payload = {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "A"}}
    inbox = _make_inbox(
        kind="REPLAY_REQUEST",
        payload_json=_replay_envelope(original_kind="DEVICE_EVENT", original_payload=original_payload),
    )

    projected = _project_shape_validated_replay(inbox)

    assert projected.is_manual_replay is True
    assert projected.replay_immediate_source_inbox_id == 8
    assert projected.payload_json == original_payload
    with pytest.raises(AttributeError):
        projected.is_manual_replay = False


def test_replay_request_projection_rejects_invalid_envelope() -> None:
    inbox = _make_inbox(kind="REPLAY_REQUEST", payload_json={"original_kind": "REPLAY_REQUEST"})

    with pytest.raises(Exception, match="INVALID_REPLAY_ENVELOPE"):
        _project_shape_validated_replay(inbox)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("original_provider_code", ""),
        ("original_workline_id", "20"),
        ("original_trace_id", 123),
    ],
)
def test_replay_request_projection_rejects_invalid_routing_evidence(field: str, value: object) -> None:
    envelope = _replay_envelope(original_kind="INTERNAL_EVENT", original_payload={"event_type": "SESSION_RESUME"})
    envelope[field] = value

    with pytest.raises(Exception, match="INVALID_REPLAY_ENVELOPE"):
        _project_shape_validated_replay(_make_inbox(kind="REPLAY_REQUEST", payload_json=envelope))


@pytest.mark.asyncio
async def test_process_claimed_marks_invalid_replay_envelope_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = _make_inbox(inbox_id=91, kind="REPLAY_REQUEST", payload_json={"original_kind": "UNKNOWN"})

    class _Repository:
        async def get_by_id(self, *_args: Any) -> Any:
            return inbox

    class _InboxService:
        error_message: str | None = None

        async def mark_failed(self, *_args: Any, **kwargs: Any) -> bool:
            self.error_message = kwargs["error_message"]
            return True

    class _Db:
        async def rollback(self) -> None:
            pass

        async def commit(self) -> None:
            pass

    service = _InboxService()

    async def _noop_diagnostic(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._record_diagnostic",
        _noop_diagnostic,
    )
    processor = RuntimeInboxProcessorBridge(
        inbox_repository=_Repository(),  # type: ignore[arg-type]
        inbox_service=service,  # type: ignore[arg-type]
    )
    created_attempts: list[str] = []
    original_create_attempt_runtime = processor.create_attempt_runtime

    def track_attempt(processor_token: str):  # type: ignore[no-untyped-def]
        created_attempts.append(processor_token)
        return original_create_attempt_runtime(processor_token)

    monkeypatch.setattr(processor, "create_attempt_runtime", track_attempt)
    result = await processor.process_claimed(_Db(), claim={"id": 91, "processor_token": "token-91"})

    assert result["failed"] == 1
    assert result["processed"] == 1
    assert created_attempts == ["token-91"]
    assert service.error_message is not None
    assert "INVALID_REPLAY_ENVELOPE" in service.error_message


@pytest.mark.asyncio
async def test_canonical_entry_replay_claim_process_bypasses_duplicate_gate_without_payload_marker(
    db_session: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical replay 身份必须由投影证据携带，original_payload 不得注入 replay 字段。"""

    class _AuditService:
        async def create_audit_log(self, *_args: Any, **_kwargs: Any) -> object:
            return SimpleNamespace(id=1)

    source = RuntimeInbox(
        kind="DEVICE_EVENT",
        provider_code="ECS",
        event_type="SCAN_COMPLETED",
        source_event_id="canonical-entry-source",
        payload_hash=_canonical_payload_hash({"event_type": "SCAN_COMPLETED", "data": {"HHPN": "MAT-REPLAY-1"}}),
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "MAT-REPLAY-1"}},
        payload_schema_version=1,
        workline_id=20,
        workline_session_id=10,
        status="DEAD_LETTER",
        claim_bucket_key="source:canonical-entry-source",
        received_at=1_700_000_000_000,
        failed_at=1_700_000_000_001,
    )
    db_session.add(source)
    await db_session.flush()
    db_session.add(
        WorklineTimeline(
            session_id=10,
            workline_id=20,
            seq_no=1,
            occurred_at=timezone.now_for_db(),
            stage=TimelineStage.MANUAL,
            action_type=TimelineActionType.MANUAL_HOLD,
            actor_type=TimelineActorType.ORCHESTRATOR,
            to_status="MANUAL_HOLD",
            status=TimelineStatus.PENDING,
            payload_json={"reason_code": "PAYLOAD_INVALID"},
            related_inbox_id=source.id,
        )
    )
    wrong_source = RuntimeInbox(
        kind="DEVICE_EVENT",
        provider_code="ECS",
        event_type="SCAN_COMPLETED",
        source_event_id="wrong-entry-source",
        payload_hash=_canonical_payload_hash({"event_type": "SCAN_COMPLETED", "data": {"HHPN": "WRONG"}}),
        payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "WRONG"}},
        payload_schema_version=1,
        workline_id=20,
        workline_session_id=10,
        status="DEAD_LETTER",
        claim_bucket_key="source:wrong-entry-source",
        received_at=1_700_000_000_002,
        failed_at=1_700_000_000_003,
    )
    db_session.add(wrong_source)
    await db_session.flush()
    runtime_service = RuntimeInboxService(audit_service=_AuditService())

    session = _make_session(status="MANUAL_HOLD")
    session.failure_code = "PAYLOAD_INVALID"
    workline = _make_workline()
    orchestrated: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []

    async def load_related(*_args: Any, **_kwargs: Any) -> tuple[object, ...]:
        return session, workline, None, None, {}, SimpleNamespace(), True

    class _Processor:
        async def process(self, *_args: Any, **kwargs: Any) -> OrchestratorResult:
            inbox = kwargs["inbox"]
            orchestrated.append(dict(inbox.payload_json))
            result = OrchestratorResult(success=True, intents=[])
            await kwargs["write_callback"](result)
            return result

    class _WriteBack:
        def build_write_callback(self, db: Any, **kwargs: Any) -> Any:
            async def write_effect(_result: OrchestratorResult) -> None:
                effects.append(dict(kwargs["inbox"].payload_json))
                kwargs["state"].write_effects_applied = True
                kwargs["state"].disposition = WriteBackDisposition.PROCESSED
                assert await runtime_service.mark_processed(
                    db,
                    inbox_id=kwargs["inbox_pk"],
                    lease_token=kwargs["processor_token"],
                )
                await db.commit()

            return write_effect

    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge._load_related_entities",
        load_related,
    )
    processor = RuntimeInboxProcessorBridge(
        processor_service=_Processor(),  # type: ignore[arg-type]
        writeback_service=_WriteBack(),  # type: ignore[arg-type]
        inbox_service=runtime_service,
    )

    wrong_replay = await runtime_service.replay_from_dead_letter(
        db_session,
        source_inbox_id=wrong_source.id,
        request_id="wrong-entry-replay",
        actor="42",
        reason="wrong dead-letter source in same session",
    )
    await db_session.flush()
    wrong_claims = await runtime_service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="wrong-replay-token",
        stale_after_seconds=60,
    )
    assert [claim["id"] for claim in wrong_claims] == [wrong_replay.replay_record.id]
    wrong_result = await processor.process_claimed(db_session, claim=wrong_claims[0])

    assert wrong_result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    assert orchestrated == []
    assert effects == []
    archived = (
        await db_session.execute(select(WorklineTimeline).where(WorklineTimeline.message == "DUPLICATE_ENTRY_ARCHIVED"))
    ).scalar_one()
    assert archived.action_type == TimelineActionType.EVENT_PROCESSED
    assert archived.related_inbox_id == wrong_replay.replay_record.id

    replay = await runtime_service.replay_from_dead_letter(
        db_session,
        source_inbox_id=source.id,
        request_id="canonical-entry-replay",
        actor="42",
        reason="payload validation corrected externally",
    )
    await db_session.flush()
    claims = await runtime_service.claim_for_processing(
        db_session,
        limit=1,
        processor_token="canonical-replay-token",
        stale_after_seconds=60,
    )
    assert [claim["id"] for claim in claims] == [replay.replay_record.id]
    assert "replay_of_event_id" not in replay.replay_record.payload_json["original_payload"]
    result = await processor.process_claimed(db_session, claim=claims[0])

    expected_payload = {"event_type": "SCAN_COMPLETED", "data": {"HHPN": "MAT-REPLAY-1"}}
    assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
    assert orchestrated == [expected_payload]
    assert effects == [expected_payload]


class TestEstopTimerRouting:
    def test_estop_routes_to_estop(self) -> None:
        outcome = RuntimeInboxValidationService().classify_estop_or_timer(
            resolved_event_type="ESTOP_PRESSED",
            inbox_kind="DEVICE_EVENT",
        )
        assert outcome.estop_event is True
        assert outcome.terminal_disposition == WriteBackDisposition.PROCESSED

    def test_timer_routes_to_timer(self) -> None:
        outcome = RuntimeInboxValidationService().classify_estop_or_timer(
            resolved_event_type="TIMER_TIMEOUT",
            inbox_kind="TIMER_TIMEOUT",
        )
        assert outcome.timer_timeout_event is True

    def test_normal_event_continues(self) -> None:
        outcome = RuntimeInboxValidationService().classify_estop_or_timer(
            resolved_event_type="SCAN_COMPLETED",
            inbox_kind="DEVICE_EVENT",
        )
        assert outcome.proceed_to_orchestrator is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    [
        None,
        RuntimeInboxManualHoldEvidence(10, "EVENT_PROCESSED", "SUCCESS", None, 8, 10, "DEAD_LETTER"),
        RuntimeInboxManualHoldEvidence(10, "MANUAL_HOLD", "PENDING", "OTHER", 8, 10, "DEAD_LETTER"),
        RuntimeInboxManualHoldEvidence(10, "MANUAL_HOLD", "PENDING", "PAYLOAD_INVALID", 8, 11, "DEAD_LETTER"),
        RuntimeInboxManualHoldEvidence(10, "MANUAL_HOLD", "PENDING", "PAYLOAD_INVALID", 8, 10, "PROCESSED"),
    ],
)
async def test_payload_invalid_replay_fails_closed_without_exact_latest_hold_evidence(evidence: object) -> None:
    """无证据、latest 非目标 hold 或 source 所有权/状态不符均不得授权。"""

    class _Repository:
        async def get_latest_manual_hold_evidence(self, *_args: Any, **_kwargs: Any) -> object:
            return evidence

    service = RuntimeInboxValidationService(inbox_repository=_Repository())  # type: ignore[arg-type]
    allowed = await service.is_payload_invalid_entry_replay(
        object(),
        inbox=SimpleNamespace(
            is_manual_replay=True,
            replay_immediate_source_inbox_id=8,
            replay_root_source_inbox_id=7,
        ),
        session=SimpleNamespace(
            id=10,
            status="MANUAL_HOLD",
            failure_code="PAYLOAD_INVALID",
            awaiting_device_command_code=None,
            current_wait_type=None,
        ),
    )

    assert allowed is False


class TestScanBarcodeHelper:
    def test_hhpn_field(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {"HHPN": "X"}}) is True

    def test_qty_string_field(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {"Qty": "1"}}) is True

    def test_empty_data(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {}}) is False

    def test_no_data_field(self) -> None:
        assert _scan_completed_has_any_barcode_payload({}) is False

    def test_no_string_value(self) -> None:
        assert _scan_completed_has_any_barcode_payload({"data": {"HHPN": ""}}) is False
        assert _scan_completed_has_any_barcode_payload({"data": {"HHPN": 123}}) is False


class TestEntryEventTypes:
    def test_default_with_no_plugin(self) -> None:
        assert "SCAN_COMPLETED" in _entry_event_types_for_workline(None)

    def test_default_with_unknown_plugin(self) -> None:
        workline = SimpleNamespace(plugin_key="non-existent")
        assert "SCAN_COMPLETED" in _entry_event_types_for_workline(workline)

    def test_definition_lookup_uses_configured_contract_version(self, monkeypatch) -> None:
        from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_validation_service as module

        seen: list[tuple[str | None, str | None]] = []

        def get_definition(plugin_key: str | None, contract_version: str | None = None) -> None:
            seen.append((plugin_key, contract_version))

        monkeypatch.setattr(module, "get_workline_capability_definition", get_definition)
        workline = SimpleNamespace(plugin_key="demo", contract_version="v2")

        assert "SCAN_COMPLETED" in _entry_event_types_for_workline(workline)
        assert seen == [("demo", "v2")]


def test_normalized_entry_material_evidence_uses_pinned_contract_version(monkeypatch) -> None:
    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as module

    seen: list[tuple[str | None, str | None]] = []

    def parse(plugin_key: str | None, payload: dict[str, Any], *, contract_version: str | None = None) -> None:
        seen.append((plugin_key, contract_version))

    monkeypatch.setattr(module, "parse_workline_six_in_one", parse)

    assert (
        _normalized_entry_material_evidence(
            plugin_key="demo",
            contract_version="v2",
            payload={"data": {}},
        )
        == {}
    )
    assert seen == [("demo", "v2")]


def test_duplicate_entry_material_conflict_uses_session_pinned_contract_version(monkeypatch) -> None:
    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as module

    seen: list[tuple[str | None, str | None]] = []

    def normalize(
        *,
        plugin_key: str | None,
        contract_version: str | None = None,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        seen.append((plugin_key, contract_version))
        return {"material_code": str(payload["material_code"])}

    monkeypatch.setattr(module, "_normalized_entry_material_evidence", normalize)
    session = SimpleNamespace(
        plugin_key="demo",
        contract_version="v2",
        context_json={"initial_payload": {"material_code": "OLD"}},
    )
    workline = SimpleNamespace(plugin_key="demo", contract_version="v3")

    assert (
        _duplicate_entry_material_conflict(
            session=session,
            workline=workline,
            payload={"material_code": "NEW"},
        )
        is not None
    )
    assert seen == [("demo", "v2"), ("demo", "v2")]


# ============================================================
# Stage 2: Orchestrator delegate
# ============================================================


class TestOrchestratorDelegate:
    @pytest.mark.asyncio
    async def test_delegate_passes_through(self) -> None:
        """纯 delegate: 应直接转发到 OrchestratorService.process_inbox."""

        class _StubOrchestrator:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def process_inbox(
                self,
                *,
                session: object,
                workline: object,
                inbox: object,
                devices_by_role: dict[str, list[Any]],
                services: object,
                trace_id: str,
                write_callback: object,
            ) -> OrchestratorResult:
                return OrchestratorResult(
                    success=True,
                    intents=[],
                    error=None,
                    error_code=None,
                    error_domain=None,
                )

        delegate = RuntimeInboxOrchestratorDelegate(orchestrator_factory=_StubOrchestrator)
        result = await delegate.process(
            db=SimpleNamespace(),
            session=SimpleNamespace(),
            workline=SimpleNamespace(),
            inbox=SimpleNamespace(id=1),
            devices_by_role={},
            services=SimpleNamespace(),
            trace_id="trace-1",
            write_callback=None,
        )
        assert result.success is True
        assert result.intents == []

    @pytest.mark.asyncio
    async def test_delegate_preserves_failure_result(self) -> None:
        """Stage 2 失败结果必须原样返回，由 composition 决定终态。"""

        class _FailingOrchestrator:
            def __init__(self, *args: object, **kwargs: object) -> None:
                _ = args, kwargs

            async def process_inbox(self, **kwargs: object) -> OrchestratorResult:
                _ = kwargs
                return OrchestratorResult(success=False, error="business rejected", error_code="BIZ_REJECTED")

        result = await RuntimeInboxOrchestratorDelegate(orchestrator_factory=_FailingOrchestrator).process(
            db=SimpleNamespace(),
            session=SimpleNamespace(),
            workline=SimpleNamespace(),
            inbox=SimpleNamespace(id=1),
            devices_by_role={},
            services=SimpleNamespace(),
            trace_id="trace-failure",
        )

        assert result.success is False
        assert result.error == "business rejected"
        assert result.error_code == "BIZ_REJECTED"

    @pytest.mark.asyncio
    async def test_delegate_enforces_timeout_boundary(self) -> None:
        """Stage 2 超时边界必须抛 TimeoutError 交给 composition 统一失败处理。"""

        class _SlowOrchestrator:
            def __init__(self, *args: object, **kwargs: object) -> None:
                _ = args, kwargs

            async def process_inbox(self, **kwargs: object) -> OrchestratorResult:
                _ = kwargs
                await asyncio.sleep(0.05)
                return OrchestratorResult(success=True, intents=[])

        delegate = RuntimeInboxOrchestratorDelegate(
            orchestrator_factory=_SlowOrchestrator,
            timeout_seconds=0.001,
        )
        with pytest.raises(TimeoutError):
            await delegate.process(
                db=SimpleNamespace(),
                session=SimpleNamespace(),
                workline=SimpleNamespace(),
                inbox=SimpleNamespace(id=1),
                devices_by_role={},
                services=SimpleNamespace(),
                trace_id="trace-timeout",
            )


# ============================================================
# Stage 3: Write-back service
# ============================================================


class TestSessionWriteSnapshot:
    def test_snapshot_extracts_status_and_awaiting(self) -> None:
        session = _make_session(status="RUNNING", awaiting_device_command_code="CMD-1")
        snap = _session_write_snapshot(session)
        assert snap[0] == "RUNNING"
        assert snap[1] == "CMD-1"

    def test_snapshot_change_detected(self) -> None:
        snap_a = _session_write_snapshot(_make_session(status="RUNNING", awaiting_device_command_code="CMD-1"))
        snap_b = _session_write_snapshot(
            _make_session(status="WAITING_DEVICE_RESULT", awaiting_device_command_code="CMD-1")
        )
        assert snap_a != snap_b


class TestIsLateOrDuplicateCommandResult:
    @pytest.mark.parametrize("kind", ["INTERNAL_EVENT", "EXTERNAL_HTTP"])
    def test_noncallback_transport_is_not_guarded_as_command_result(self, kind: str) -> None:
        inbox = _make_inbox(kind=kind, payload_json={"logical_route": "SCAN_COMPLETED"})
        session = _make_session(status="WAITING_DEVICE_RESULT", awaiting_device_command_code="CMD-1")
        assert (
            _is_late_or_duplicate_command_result_for_session(
                inbox=inbox,
                payload=inbox.payload_json,
                session=session,
                command=None,
            )
            is False
        )

    @pytest.mark.parametrize("kind", ["INTERNAL_EVENT", "EXTERNAL_HTTP"])
    def test_pick_result_callback_missing_correlation_is_evidence_only(self, kind: str) -> None:
        inbox = _make_inbox(
            kind=kind,
            payload_json={"logical_route": "PICK_AND_PUT_RESULT", "result": "SUCCESS"},
        )
        session = _make_session(status="WAITING_DEVICE_RESULT", awaiting_device_command_code="CMD-1")
        assert (
            _is_late_or_duplicate_command_result_for_session(
                inbox=inbox,
                payload=inbox.payload_json,
                session=session,
                command=None,
            )
            is True
        )

    @pytest.mark.parametrize("kind", ["INTERNAL_EVENT", "EXTERNAL_HTTP"])
    def test_pick_result_callback_mismatch_is_evidence_only(self, kind: str) -> None:
        inbox = _make_inbox(
            kind=kind,
            payload_json={"callback_type": "PICK_AND_PUT_RESULT", "command_code": "CMD-OTHER"},
        )
        session = _make_session(status="WAITING_DEVICE_RESULT", awaiting_device_command_code="CMD-1")
        command = SimpleNamespace(command_code="CMD-OTHER", status="PENDING")
        assert (
            _is_late_or_duplicate_command_result_for_session(
                inbox=inbox,
                payload=inbox.payload_json,
                session=session,
                command=command,
            )
            is True
        )

    def test_missing_callback_correlation_is_evidence_only(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(status="WAITING_DEVICE_RESULT", awaiting_device_command_code="CMD-1")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=None)
            is True
        )

    def test_non_matching_callback_is_evidence_only_before_terminal_update(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(status="WAITING_DEVICE_RESULT", awaiting_device_command_code="CMD-1")
        command = SimpleNamespace(command_code="CMD-OTHER", status="PENDING")
        assert (
            _is_late_or_duplicate_command_result_for_session(
                inbox=inbox,
                payload={"command_code": "CMD-OTHER"},
                session=session,
                command=command,
            )
            is True
        )

    def test_terminal_session_is_late(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(status="COMPLETED")
        command = SimpleNamespace(command_code="CMD-1", status="COMPLETED")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is True
        )

    def test_running_session_with_matching_command(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(
            status="WAITING_DEVICE_RESULT",
            awaiting_device_command_code="CMD-1",
        )
        command = SimpleNamespace(command_code="CMD-1", status="COMPLETED")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is False
        )

    def test_non_command_result_kind_never_late(self) -> None:
        inbox = _make_inbox(kind="DEVICE_EVENT")
        session = _make_session(status="COMPLETED")
        command = SimpleNamespace(command_code="CMD-1", status="COMPLETED")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is False
        )

    def test_non_terminal_command_status(self) -> None:
        inbox = _make_inbox(kind="COMMAND_RESULT")
        session = _make_session(status="RUNNING", awaiting_device_command_code="CMD-1")
        command = SimpleNamespace(command_code="CMD-1", status="PENDING")
        assert (
            _is_late_or_duplicate_command_result_for_session(inbox=inbox, payload={}, session=session, command=command)
            is False
        )


class TestArchiveTimelineSequence:
    @pytest.mark.asyncio
    async def test_duplicate_and_late_archives_delegate_sequence_allocation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同 session 多次归档必须由 timeline service 分配 seq_no。"""
        requested_seq_nos: list[int | None] = []

        async def add_with_sequence(db: object, timeline: object, *, seq_no: int | None = None) -> int:
            _ = db, timeline
            requested_seq_nos.append(seq_no)
            return len(requested_seq_nos)

        monkeypatch.setattr(
            "src.app.runtime.orchestration.services.trace.timeline_sequence_service.add_timeline_with_sequence",
            add_with_sequence,
        )
        session = _make_session()
        workline = _make_workline()
        command = SimpleNamespace(id=99, command_code="CMD-1", status="COMPLETED")
        for inbox_id in (1, 2):
            inbox = _make_inbox(inbox_id=inbox_id)
            await _record_duplicate_entry_archive_timeline(
                SimpleNamespace(),
                session=session,
                workline=workline,
                inbox=inbox,
                payload=inbox.payload_json,
                reason="DUPLICATE",
            )
            await _record_late_command_result_archive_timeline(
                SimpleNamespace(),
                session=session,
                workline=workline,
                inbox=inbox,
                command=command,
                payload=inbox.payload_json,
                reason="LATE",
            )

        assert requested_seq_nos == [None, None, None, None]


class TestResultRequiresOutboxDispatch:
    def test_empty_result(self) -> None:
        assert _result_requires_outbox_dispatch(OrchestratorResult(success=True, intents=[])) is False

    def test_command_intent(self) -> None:
        from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind

        intent = RuntimeIntent(
            kind=RuntimeIntentKind.COMMAND,
            action="PICK",
            payload={"x": 1},
            result_policy="COMMAND_RESULT",
        )
        assert _result_requires_outbox_dispatch(OrchestratorResult(success=True, intents=[intent])) is True

    def test_continue_next_with_action(self) -> None:
        from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind

        intent = RuntimeIntent(kind=RuntimeIntentKind.CONTINUE_NEXT, action="RESUME")
        assert _result_requires_outbox_dispatch(OrchestratorResult(success=True, intents=[intent])) is True


class TestBuildWriteCallback:
    @pytest.mark.asyncio
    async def test_write_callback_orchestrator_writes_processed(self) -> None:
        """write-back 正常路径: 业务 effect 返回 PROCESSED → mark_as_processed."""
        from contextlib import suppress

        inbox = _make_inbox()
        session = _make_session()
        workline = _make_workline()
        command = SimpleNamespace(command_code="CMD-1", status="PENDING")

        class _FakeDb:
            async def refresh(self, value: object) -> None:
                _ = value

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                pass

        class _FakeWriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                return RuntimeIntentEffectResult.processed()

        class _FakeInboxService:
            def __init__(self) -> None:
                self.mark_processed_calls: list[dict[str, Any]] = []

            async def mark_processed(
                self,
                db: object,
                *,
                inbox_id: int,
                lease_token: str,
            ) -> object:
                self.mark_processed_calls.append({"inbox_id": inbox_id, "lease_token": lease_token})
                return SimpleNamespace(id=inbox_id)

        state = WriteBackState()
        write_callback = RuntimeInboxWriteBackService(
            write_back_service=_FakeWriteBack(),
            inbox_service=_FakeInboxService(),
        ).build_write_callback(
            db=_FakeDb(),
            session=session,
            workline=workline,
            inbox=inbox,
            devices_by_role={},
            device=None,
            command=command,
            inbox_pk=1,
            session_snapshot=_session_write_snapshot(session),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="token-1",
            state=state,
        )

        result = OrchestratorResult(success=True, intents=[])
        with suppress(Exception):
            await write_callback(result)

        # 验证: state.disposition == PROCESSED + write_effects_applied = True
        assert state.disposition == WriteBackDisposition.PROCESSED
        assert state.write_effects_applied is True

    @pytest.mark.asyncio
    async def test_write_callback_resource_retry_marks_retryable_failure(self) -> None:
        """Stage 3 RESOURCE_RETRY 必须携带 lease token 写 retryable FAILED。"""
        inbox = _make_inbox()
        session = _make_session()
        calls: list[dict[str, Any]] = []

        class _Db:
            async def refresh(self, value: object) -> None:
                _ = value

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                pass

        class _WriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                _ = args, kwargs
                return RuntimeIntentEffectResult.resource_retry()

        class _InboxService:
            async def mark_failed(self, db: object, **kwargs: object) -> bool:
                _ = db
                calls.append(dict(kwargs))
                return True

        state = WriteBackState()
        callback = RuntimeInboxWriteBackService(
            write_back_service=_WriteBack(),
            inbox_service=_InboxService(),
        ).build_write_callback(
            _Db(),
            session=session,
            workline=_make_workline(),
            inbox=inbox,
            devices_by_role={},
            device=None,
            command=None,
            inbox_pk=1,
            session_snapshot=_session_write_snapshot(session),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="lease-resource",
            state=state,
        )

        await callback(OrchestratorResult(success=True, intents=[]))

        assert calls == [
            {
                "inbox_id": 1,
                "lease_token": "lease-resource",
                "error_code": "RESOURCE_WAIT",
                "error_message": "RESOURCE_WAIT",
                "retryable": True,
                "consume_attempt": False,
            }
        ]
        assert state.disposition == WriteBackDisposition.RESOURCE_RETRY
        assert state.write_effects_applied is True

    @pytest.mark.asyncio
    async def test_write_callback_rejects_stale_session_before_effects(self) -> None:
        """Stage 3 stale snapshot 必须在业务 effect 和终态写入前拒绝。"""
        inbox = _make_inbox()
        session = _make_session(status="RUNNING")
        writeback_called = False
        rollbacks = 0

        class _Db:
            async def refresh(self, value: object) -> None:
                value.status = "WAITING_DEVICE_RESULT"

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                nonlocal rollbacks
                rollbacks += 1

        class _WriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                nonlocal writeback_called
                _ = args, kwargs
                writeback_called = True
                return RuntimeIntentEffectResult.processed()

        state = WriteBackState()
        callback = RuntimeInboxWriteBackService(
            write_back_service=_WriteBack(),
            inbox_service=SimpleNamespace(),
        ).build_write_callback(
            _Db(),
            session=session,
            workline=_make_workline(),
            inbox=inbox,
            devices_by_role={},
            device=None,
            command=None,
            inbox_pk=1,
            session_snapshot=("RUNNING", None),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="lease-stale",
            state=state,
        )

        with pytest.raises(RuntimeError, match="refusing stale orchestrator effects"):
            await callback(OrchestratorResult(success=True, intents=[]))

        assert writeback_called is False
        assert rollbacks == 1
        assert state.write_effects_applied is False

    @pytest.mark.asyncio
    async def test_write_callback_rolls_back_effect_failure(self) -> None:
        """Stage 3 业务 effect 失败必须回滚且不伪造终态。"""
        session = _make_session()
        rollbacks = 0

        class _Db:
            async def refresh(self, value: object) -> None:
                _ = value

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                nonlocal rollbacks
                rollbacks += 1

        class _WriteBack:
            async def write_back(self, *args: object, **kwargs: object) -> RuntimeIntentEffectResult:
                _ = args, kwargs
                raise RuntimeError("effect failed")

        state = WriteBackState()
        callback = RuntimeInboxWriteBackService(
            write_back_service=_WriteBack(),
            inbox_service=SimpleNamespace(),
        ).build_write_callback(
            _Db(),
            session=session,
            workline=_make_workline(),
            inbox=_make_inbox(),
            devices_by_role={},
            device=None,
            command=None,
            inbox_pk=1,
            session_snapshot=_session_write_snapshot(session),
            sse_workline_id=20,
            sse_session_id=10,
            processor_token="lease-effect-failure",
            state=state,
        )

        with pytest.raises(RuntimeError, match="effect failed"):
            await callback(OrchestratorResult(success=True, intents=[]))

        assert rollbacks == 1
        assert state.write_effects_applied is False


# ============================================================
# Internal helpers
# ============================================================
