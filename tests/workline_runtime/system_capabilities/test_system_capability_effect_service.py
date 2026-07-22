"""通用 SYSTEM_CAPABILITY EFFECT coordinator 合同。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
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
from src.app.runtime.system_capabilities.device.device_command_write.contracts import (
    DeviceCommandWriteInput,
    DeviceCommandWriteOutput,
)
from src.app.runtime.system_capabilities.device.device_command_write.definition import DEFINITION as DEVICE_DEFINITION
from src.app.runtime.system_capabilities.material_flow.material_unit_write.contracts import MaterialUnitWriteOutput
from src.app.runtime.system_capabilities.material_flow.material_unit_write.definition import (
    DEFINITION as MATERIAL_DEFINITION,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.system_capabilities.runtime.session_hold.contracts import SessionHoldOutput
from src.app.runtime.system_capabilities.runtime.session_hold.definition import DEFINITION as HOLD_DEFINITION


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


class _DatabaseFailingHandler:
    async def __call__(self, request: _Input, *, execution: object) -> object:
        _ = request, execution
        raise IntegrityError("INSERT device_commands", {}, RuntimeError("duplicate command identity"))


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
    def __init__(self, result: ClaimResult | BaseException, *, success_evidence: object | None = None) -> None:
        self.result = result
        self.success_evidence = success_evidence
        self.calls: list[dict[str, Any]] = []
        self.outcomes: list[object] = []
        self.intent_log = SimpleNamespace(effect_status=RuntimeIntentStatus.PROPOSED, dispatch_key=None)

    async def claim_or_match(self, _db: object, **kwargs: Any) -> ClaimResult:
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def record_outcome(self, _db: object, **kwargs: Any) -> None:
        evidence = kwargs["evidence"]
        self.outcomes.append(evidence)
        self.intent_log.effect_status = {
            "success": RuntimeIntentStatus.COMPLETED,
            "business_reject": RuntimeIntentStatus.REJECTED,
            "retryable_failure": RuntimeIntentStatus.TECHNICAL_FAILED,
            "contract_violation": RuntimeIntentStatus.TECHNICAL_FAILED,
        }[evidence.outcome_kind]

    async def get_claimed_intent(self, _db: object, *, claim: dict[str, Any]) -> object:
        self.intent_log.dispatch_key = claim["dispatch_key"]
        return self.intent_log

    async def get_success_evidence(self, _db: object, **_kwargs: Any) -> object | None:
        return self.success_evidence

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


class _AsyncAcceptedReplayRepository(_EffectRepository):
    """模拟同一 OUTBOX_ASYNC effect 的第二次 claim 命中已有 PROPOSED ledger。"""

    def __init__(self) -> None:
        super().__init__(ClaimResult.NEW)
        self._claim_count = 0

    async def claim_or_match(self, _db: object, **kwargs: Any) -> ClaimResult:
        self.calls.append(kwargs)
        self._claim_count += 1
        return ClaimResult.NEW if self._claim_count == 1 else ClaimResult.MATCH


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


class _SynchronousFlushDb(_Db):
    def flush(self) -> None:
        self.flush_count += 1


class _MutationDb(_Db):
    def __init__(self) -> None:
        super().__init__()
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: tuple[object, ...]) -> None:
        self.added.extend(values)

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
        dispatch_key="system-capability:test.effect:operation-1",
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
        "session": SimpleNamespace(id=31, workline_id=3, contract_version="v1", **pin),
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
    assert _RecordingHandler.calls[0][1].admission.fact_version == "fact:3"  # type: ignore[attr-defined]
    assert _RecordingHandler.calls[0][1].admission.precondition == {"expected": 3}  # type: ignore[attr-defined]
    assert db.flush_count == 1
    assert db.commit_count == 0
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_local_transactional_effect_accepts_synchronous_flush() -> None:
    _RecordingHandler.calls.clear()
    db = _SynchronousFlushDb()

    result = await _service(_definition(), _EffectRepository(ClaimResult.NEW)).apply(_ctx(db), _intent())

    assert isinstance(result.outcome, Success)
    assert db.flush_count == 1


@pytest.mark.parametrize(
    "field_name",
    ["plugin_key", "contract_version", "plugin_binding_id", "plugin_binding_version", "plugin_index_digest"],
)
def test_effect_intent_rejects_incomplete_locked_plugin_pin(field_name: str) -> None:
    definition = _definition()
    ctx = _ctx()
    setattr(ctx["session"], field_name, None)

    with pytest.raises(PermissionError, match="locked plugin pin is incomplete"):
        _service(definition, _EffectRepository(ClaimResult.NEW))._intent_service._validate_execution_identity(
            ctx, _intent(), definition=definition
        )


@pytest.mark.asyncio
async def test_outbox_async_success_keeps_intent_proposed_until_followup_evidence() -> None:
    definition = _definition(completion_mode=EffectCompletionMode.OUTBOX_ASYNC)
    repository = _EffectRepository(ClaimResult.NEW)
    result = await _service(definition, repository).apply(_ctx(), _intent())

    assert isinstance(result.outcome, Success)
    assert result.durably_accepted is True
    assert result.remote_completed is False
    assert result.evidence is None
    assert repository.outcomes == []
    assert repository.intent_log.effect_status is RuntimeIntentStatus.PROPOSED


@pytest.mark.asyncio
async def test_outbox_async_match_replays_durable_acceptance_without_handler_or_evidence() -> None:
    definition = _definition(completion_mode=EffectCompletionMode.OUTBOX_ASYNC)
    repository = _AsyncAcceptedReplayRepository()
    _RecordingHandler.calls.clear()
    service = _service(definition, repository)

    first = await service.apply(_ctx(), _intent())
    replay = await service.apply(_ctx(), _intent())

    assert isinstance(first.outcome, Success)
    assert isinstance(replay.outcome, Success)
    assert replay.outcome.payload is None
    assert replay.durably_accepted is True
    assert replay.remote_completed is False
    assert replay.idempotent_replay is True
    assert replay.evidence is None
    assert len(_RecordingHandler.calls) == 1
    assert repository.outcomes == []
    assert repository.intent_log.effect_status is RuntimeIntentStatus.PROPOSED


@pytest.mark.asyncio
async def test_outbox_async_retryable_failure_does_not_finalize_intent() -> None:
    definition = _definition(_FailingHandler, completion_mode=EffectCompletionMode.OUTBOX_ASYNC)
    repository = _EffectRepository(ClaimResult.NEW)

    result = await _service(definition, repository).apply(_ctx(), _intent())

    assert isinstance(result.outcome, RetryableFailure)
    assert result.retryable is True
    assert result.evidence is None
    assert repository.outcomes == []
    assert repository.intent_log.effect_status is RuntimeIntentStatus.PROPOSED


@pytest.mark.asyncio
async def test_same_final_key_and_hash_is_noop_success() -> None:
    _RecordingHandler.calls.clear()
    repository = _EffectRepository(
        ClaimResult.MATCH,
        success_evidence={
            "capability_key": "test.effect",
            "contract_version": "v1",
            "operation_key": "operation-1",
            "idempotency_key": "system-capability:test.effect@v1:session:31:work-item:41:operation-1",
            "payload_hash": _intent().payload_hash,
            "outcome_kind": "success",
            "outcome_code": "SUCCESS",
            "outcome": {"kind": "success", "payload": {"accepted": True}},
            "occurred_at_ms": 1000,
        },
    )
    result = await _service(_definition(), repository).apply(_ctx(), _intent())

    assert isinstance(result.outcome, Success)
    assert result.outcome.payload == _Output(accepted=True)
    assert result.idempotent_replay is True
    assert _RecordingHandler.calls == []
    [claim] = repository.calls
    assert "test.effect@v1" in claim["idempotency_key"]
    assert "session:31" in claim["idempotency_key"]
    assert "work-item:41" in claim["idempotency_key"]
    assert claim["idempotency_key"].endswith(":operation-1")
    assert claim["dispatch_key"] == "system-capability:test.effect:operation-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("definition", "input_payload", "precondition", "fact_version", "payload", "expected"),
    [
        (
            MATERIAL_DEFINITION,
            {
                "operation": "CREATE",
                "pkg_code": "PKG-1",
                "material_identity_key": "material-1",
                "six_in_one": {},
                "status": "IN_STORAGE",
            },
            {"expected_absent": True},
            "material-unit:v0",
            {"material_unit_id": 101, "status": "IN_STORAGE"},
            MaterialUnitWriteOutput(material_unit_id=101, status="IN_STORAGE"),
        ),
        (
            DEVICE_DEFINITION,
            {"target_device_id": 7, "action": "MOVE", "result_policy": "COMMAND_RESULT"},
            {"expected_available": True},
            "device:v1",
            {"accepted": True, "command_code": "CMD-1", "dispatch_key": "dispatch-1"},
            DeviceCommandWriteOutput(accepted=True, command_code="CMD-1", dispatch_key="dispatch-1"),
        ),
        (
            HOLD_DEFINITION,
            {"reason_code": "MANUAL_REVIEW", "message": "review required"},
            {"expected_status": "RUNNING"},
            "session:1",
            {"held": True, "reason_code": "MANUAL_REVIEW"},
            SessionHoldOutput(held=True, reason_code="MANUAL_REVIEW"),
        ),
    ],
)
async def test_match_replays_persisted_typed_success_for_real_effect_definitions(
    definition: SystemCapabilityDefinition,
    input_payload: dict[str, object],
    precondition: dict[str, object],
    fact_version: str,
    payload: dict[str, object],
    expected: BaseModel,
) -> None:
    intent = RuntimeIntent.system_capability(
        capability_key=definition.capability_key,
        contract_version=definition.contract_version,
        operation_key="real-operation-1",
        dispatch_key=f"system-capability:{definition.capability_key}:real-operation-1",
        payload=input_payload,
        precondition=precondition,
        fact_version=fact_version,
        timeout_seconds=definition.timeout_seconds,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 9, "binding_version": 1},
        provider_snapshot={"provider_code": "RUNTIME", "profile": definition.admission},
    )
    repository = _EffectRepository(
        ClaimResult.MATCH,
        success_evidence={
            "capability_key": definition.capability_key,
            "contract_version": definition.contract_version,
            "operation_key": "real-operation-1",
            "idempotency_key": (
                f"system-capability:{definition.capability_key}@{definition.contract_version}:"
                "session:31:work-item:41:real-operation-1"
            ),
            "payload_hash": intent.payload_hash,
            "outcome_kind": "success",
            "outcome_code": "SUCCESS",
            "outcome": {"kind": "success", "payload": payload},
            "occurred_at_ms": 1000,
        },
    )
    result = await _service(definition, repository).apply(_ctx(), intent)

    assert result.outcome == Success(payload=expected)
    assert result.idempotent_replay is True
    assert result.evidence is not None
    assert result.evidence.outcome == {"kind": "success", "payload": payload}


@pytest.mark.asyncio
async def test_match_with_invalid_persisted_success_fails_closed() -> None:
    repository = _EffectRepository(
        ClaimResult.MATCH,
        success_evidence={
            "capability_key": "test.effect",
            "contract_version": "v1",
            "operation_key": "operation-1",
            "idempotency_key": "system-capability:test.effect@v1:session:31:work-item:41:operation-1",
            "payload_hash": _intent().payload_hash,
            "outcome_kind": "success",
            "outcome_code": "SUCCESS",
            "outcome": {"kind": "success", "payload": {"accepted": "not-a-bool"}},
            "occurred_at_ms": 1000,
        },
    )

    result = await _service(_definition(), repository).apply(_ctx(), _intent())

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "PERSISTED_OUTCOME_INVALID"


def _material_intent(*, precondition: dict[str, object], fact_version: object) -> RuntimeIntent:
    return RuntimeIntent.system_capability(
        capability_key=MATERIAL_DEFINITION.capability_key,
        contract_version=MATERIAL_DEFINITION.contract_version,
        operation_key="scan:PKG-STRICT:create",
        dispatch_key="system-capability:material:create:PKG-STRICT",
        payload={
            "operation": "CREATE",
            "pkg_code": "PKG-STRICT",
            "material_identity_key": "MAT-STRICT",
            "six_in_one": {},
            "status": "IN_TRANSIT",
        },
        precondition=precondition,
        fact_version=fact_version,  # type: ignore[arg-type]
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 9, "binding_version": 1},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("precondition", "fact_version"),
    [
        ({"expected_absent": "false"}, "material-unit:v0"),
        ({"expected_absent": 1}, "material-unit:v0"),
        ({"expected_absent": False}, "malformed-version"),
    ],
)
async def test_material_admission_rejects_malformed_precondition_and_fact_version(
    precondition: dict[str, object], fact_version: object
) -> None:
    repository = _EffectRepository(ClaimResult.NEW)

    result = await _service(MATERIAL_DEFINITION, repository).apply(
        _ctx(), _material_intent(precondition=precondition, fact_version=fact_version)
    )

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_CONTRACT_INVALID"
    assert repository.calls == []


@pytest.mark.asyncio
async def test_material_string_false_never_coerces_to_expected_absent_true() -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    result = await _service(MATERIAL_DEFINITION, repository).apply(
        _ctx(), _material_intent(precondition={"expected_absent": "false"}, fact_version="material-unit:v0")
    )

    assert isinstance(result.outcome, ContractViolation)
    assert repository.calls == []


@pytest.mark.asyncio
async def test_material_legal_fact_version_mismatch_is_business_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import import_module

    from src.app.runtime.orchestration.models.material_unit import MaterialUnitStatus
    from src.app.runtime.orchestration.services.material_unit_mutation_service import MaterialUnitMutationService

    mutation_module = import_module("src.app.runtime.orchestration.services.material_unit_mutation_service")

    class _Repository:
        async def get_by_pkg_code_for_update(self, _db: object, _pkg_code: str) -> object:
            return SimpleNamespace(
                id=101,
                version=2,
                status=MaterialUnitStatus.IN_TRANSIT,
                current_session_id=None,
                six_in_one={},
            )

    monkeypatch.setattr(
        mutation_module,
        "material_unit_mutation_service",
        MaterialUnitMutationService(repository=_Repository()),  # type: ignore[arg-type]
    )
    result = await _service(MATERIAL_DEFINITION, _EffectRepository(ClaimResult.NEW)).apply(
        _ctx(), _material_intent(precondition={"expected_absent": False}, fact_version="material-unit:v1")
    )

    assert isinstance(result.outcome, BusinessReject)
    assert result.outcome.reason_code == "STALE_PRECONDITION"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_key", ["bad key", "x" * 161])
async def test_admission_rejects_bypassed_invalid_operation_key_as_contract_violation(operation_key: str) -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    intent = _intent().model_copy(update={"operation_key": operation_key})

    result = await _service(_definition(), repository).apply(_ctx(), intent)

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_CONTRACT_INVALID"
    assert repository.calls == []


@pytest.mark.asyncio
async def test_admission_revalidates_model_copy_bypassed_top_level_result_policy() -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    intent = _intent().model_copy(update={"result_policy": "COMMAND_RESULT"})

    result = await _service(_definition(), repository).apply(_ctx(), intent)

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_CONTRACT_INVALID"
    assert repository.calls == []


def _session_hold_intent(*, expected_status: object, fact_version: object) -> RuntimeIntent:
    return RuntimeIntent.system_capability(
        capability_key=HOLD_DEFINITION.capability_key,
        contract_version=HOLD_DEFINITION.contract_version,
        operation_key="session:hold:review",
        dispatch_key="system-capability:session:hold:review",
        payload={"reason_code": "REVIEW", "message": "manual review"},
        precondition={"expected_status": expected_status},
        fact_version=fact_version,  # type: ignore[arg-type]
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 9, "binding_version": 1},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_status", "fact_version"),
    [("READY", "session:3"), ("RUNNING", "session:2")],
)
async def test_session_hold_authoritative_status_or_version_mismatch_has_zero_mutation(
    monkeypatch: pytest.MonkeyPatch, expected_status: str, fact_version: str
) -> None:
    from importlib import import_module

    from src.app.runtime.orchestration.services.session_hold_mutation_service import SessionHoldMutationService

    class _Repository:
        calls = 0

        async def persist(self, _db: object, _session: object) -> None:
            self.calls += 1

    repository = _Repository()
    mutation_module = import_module("src.app.runtime.orchestration.services.session_hold_mutation_service")
    monkeypatch.setattr(mutation_module, "session_hold_mutation_service", SessionHoldMutationService(repository))
    ctx = _ctx()
    ctx["session"].status = "RUNNING"
    ctx["session"].version = 3

    result = await _service(HOLD_DEFINITION, _EffectRepository(ClaimResult.NEW)).apply(
        ctx, _session_hold_intent(expected_status=expected_status, fact_version=fact_version)
    )

    assert isinstance(result.outcome, BusinessReject)
    assert result.outcome.reason_code == "STALE_PRECONDITION"
    assert repository.calls == 0
    assert ctx["session"].status == "RUNNING"


@pytest.mark.asyncio
async def test_session_hold_matching_typed_admission_mutates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import import_module

    from src.app.runtime.orchestration.services.session_hold_mutation_service import SessionHoldMutationService

    class _Repository:
        calls = 0

        async def persist(self, _db: object, _session: object) -> None:
            self.calls += 1

    repository = _Repository()
    mutation_module = import_module("src.app.runtime.orchestration.services.session_hold_mutation_service")
    monkeypatch.setattr(mutation_module, "session_hold_mutation_service", SessionHoldMutationService(repository))
    ctx = _ctx()
    ctx["session"].status = "RUNNING"
    ctx["session"].version = 3
    ctx["session"].current_wait_type = None
    ctx["session"].waiting_since = None
    ctx["session"].deadline_at = None
    ctx["session"].current_wait_timeout_seconds = None
    ctx["session"].awaiting_device_command_code = None
    ctx["session"].ended_at = None

    result = await _service(HOLD_DEFINITION, _EffectRepository(ClaimResult.NEW)).apply(
        ctx, _session_hold_intent(expected_status="RUNNING", fact_version="session:3")
    )

    assert isinstance(result.outcome, Success)
    assert repository.calls == 1
    assert getattr(ctx["session"].status, "value", ctx["session"].status) == "MANUAL_HOLD"


def _device_intent(*, expected_available: object, fact_version: object) -> RuntimeIntent:
    return RuntimeIntent.system_capability(
        capability_key=DEVICE_DEFINITION.capability_key,
        contract_version=DEVICE_DEFINITION.contract_version,
        operation_key="device:71:dispatch",
        dispatch_key="device-command:CMD-71-EFFECT",
        payload={
            "target_device_id": 71,
            "action": "PICK_AND_PUT",
            "command_code": "CMD-71-EFFECT",
            "result_policy": "COMMAND_RESULT",
        },
        precondition={"expected_available": expected_available},
        fact_version=fact_version,  # type: ignore[arg-type]
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 9, "binding_version": 1},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_values", "fact_version"),
    [
        ({"version": 2, "device_status": "IDLE", "maintenance_mode": False, "current_command_id": None}, "device:v1"),
        ({"version": 2, "device_status": "RUNNING", "maintenance_mode": False, "current_command_id": 99}, "device:v2"),
        ({"version": 2, "device_status": "IDLE", "maintenance_mode": True, "current_command_id": None}, "device:v2"),
    ],
)
async def test_device_authoritative_fact_mismatch_creates_no_command_or_outbox(
    monkeypatch: pytest.MonkeyPatch, device_values: dict[str, object], fact_version: str
) -> None:
    from importlib import import_module

    from src.app.runtime.orchestration.services.device_command_gateway import StaleRuntimeDeviceCommandAdmission

    calls: list[dict[str, object]] = []

    async def prepare(*_args: object, **_kwargs: object) -> tuple[object, object]:
        calls.append(_kwargs)
        raise StaleRuntimeDeviceCommandAdmission("device fact changed")

    gateway_module = import_module("src.app.runtime.orchestration.services.device_command_gateway")
    monkeypatch.setattr(gateway_module, "prepare_runtime_device_command_effect", prepare)
    device = SimpleNamespace(id=71, is_active=True, **device_values)
    ctx = _ctx()
    ctx["source_device"] = device

    result = await _service(DEVICE_DEFINITION, _EffectRepository(ClaimResult.NEW)).apply(
        ctx, _device_intent(expected_available=True, fact_version=fact_version)
    )

    assert isinstance(result.outcome, BusinessReject)
    assert result.outcome.reason_code == "STALE_PRECONDITION"
    assert len(calls) == 1
    assert calls[0]["target_device_id"] == 71
    assert calls[0]["admission"].fact_version == fact_version  # type: ignore[union-attr]
    assert calls[0]["expected_workline_id"] == 3


@pytest.mark.asyncio
async def test_device_matching_typed_admission_creates_one_command_and_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import import_module

    calls: list[dict[str, object]] = []

    async def prepare(*_args: object, **_kwargs: object) -> tuple[object, object]:
        calls.append(_kwargs)
        return SimpleNamespace(command_code="CMD-71"), SimpleNamespace(dispatch_key="dispatch-71")

    gateway_module = import_module("src.app.runtime.orchestration.services.device_command_gateway")
    monkeypatch.setattr(gateway_module, "prepare_runtime_device_command_effect", prepare)
    ctx = _ctx()
    ctx["source_device"] = SimpleNamespace(
        id=71,
        is_active=True,
        version=2,
        device_status="IDLE",
        maintenance_mode=False,
        current_command_id=None,
    )

    effect_repository = _EffectRepository(ClaimResult.NEW)
    result = await _service(DEVICE_DEFINITION, effect_repository).apply(
        ctx, _device_intent(expected_available=True, fact_version="device:v2")
    )

    assert isinstance(result.outcome, Success)
    assert result.outcome.payload.command_code == "CMD-71"
    assert len(calls) == 1
    assert calls[0]["target_device_id"] == 71
    assert calls[0]["admission"].fact_version == "device:v2"  # type: ignore[union-attr]
    assert calls[0]["expected_workline_id"] == 3
    assert calls[0]["intent_log"] is effect_repository.intent_log


@pytest.mark.asyncio
async def test_device_command_without_runtime_workline_identity_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import import_module

    async def prepare(*_args: object, **_kwargs: object) -> tuple[object, object]:
        raise AssertionError("missing workline identity must not reach gateway")

    gateway_module = import_module("src.app.runtime.orchestration.services.device_command_gateway")
    monkeypatch.setattr(gateway_module, "prepare_runtime_device_command_effect", prepare)
    ctx = _ctx()
    delattr(ctx["session"], "workline_id")

    result = await _service(DEVICE_DEFINITION, _EffectRepository(ClaimResult.NEW)).apply(
        ctx, _device_intent(expected_available=True, fact_version="device:v2")
    )

    assert isinstance(result.outcome, BusinessReject)
    assert result.outcome.reason_code == "WORKLINE_SCOPE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        _session_hold_intent(expected_status="RUNNING", fact_version="session:v3"),
        _device_intent(expected_available="true", fact_version="device:v2"),
    ],
)
async def test_session_and_device_malformed_admission_remain_contract_violations(intent: RuntimeIntent) -> None:
    repository = _EffectRepository(ClaimResult.NEW)
    definition = HOLD_DEFINITION if intent.capability_key == HOLD_DEFINITION.capability_key else DEVICE_DEFINITION

    result = await _service(definition, repository).apply(_ctx(), intent)

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_CONTRACT_INVALID"
    assert repository.calls == []


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
async def test_handler_database_error_propagates_without_recording_outcome() -> None:
    repository = _EffectRepository(ClaimResult.NEW)

    with pytest.raises(IntegrityError, match="duplicate command identity"):
        await _service(_definition(_DatabaseFailingHandler), repository).apply(_ctx(), _intent())

    assert repository.outcomes == []


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
    from src.app.device.repositories.command_repository import DeviceCommandRepository
    from src.app.device.repositories.device_repository import DeviceRepository
    from src.app.device.services.device_command_service import DeviceCommandService
    from src.app.sys.models import SystemOutbox

    class _DeviceRepository(DeviceRepository):
        async def get_runtime_effect_target_for_update(
            self,
            _db: object,
            *,
            target_device_id: int | None,
            target_device_code: str | None,
            expected_workline_id: int,
        ) -> object:
            assert target_device_id == 71
            assert target_device_code is None
            assert expected_workline_id == 41
            return SimpleNamespace(
                id=71,
                device_code="ARM-71",
                work_line_id=41,
                version=2,
                device_status="IDLE",
                maintenance_mode=False,
                current_command_id=None,
                is_active=True,
            )

    class _CommandRepository(DeviceCommandRepository):
        async def get_unfinished_commands_for_device(
            self,
            _db: object,
            device_id: int,
            *,
            limit: int = 1,
        ) -> list[DeviceCommand]:
            assert device_id == 71
            assert limit == 1
            return []

    db = _MutationDb()
    session = SimpleNamespace(
        id=31,
        workline_id=41,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        status="RUNNING",
        current_wait_type=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
        awaiting_device_command_code=None,
    )
    intent_log = SimpleNamespace(
        effect_status=RuntimeIntentStatus.PROPOSED,
        dispatch_key="device-command:CMD-RUNTIME-EFFECT",
    )
    command, outbox = await DeviceCommandService(
        repository=_CommandRepository(),
        device_repository=_DeviceRepository(),
    ).prepare_runtime_effect(
        db,  # type: ignore[arg-type]
        request=SimpleNamespace(
            action="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            payload={"business_key": "PKG-1"},
            command_code="CMD-RUNTIME-EFFECT",
            result_policy="COMMAND_RESULT",
        ),
        target_device_id=71,
        target_device_code=None,
        expected_workline_id=41,
        expected_fact_version="device:v2",
        expected_available=True,
        session=session,
        workline=SimpleNamespace(id=41, plugin_key="rough_sorter"),
        idempotency_key="system-capability:device.device_command_write@v1:session:31:work-item:41:pick-1",
        execution_correlation_id="corr-device-effect",
        trace_id="trace-device-effect",
        intent_log=intent_log,
    )

    assert isinstance(command, DeviceCommand)
    assert isinstance(outbox, SystemOutbox)
    assert command.correlation_id == "corr-device-effect"
    assert outbox.dispatch_key == f"device-command:{command.command_code}"
    assert getattr(outbox.status, "value", outbox.status) == "NEW"
    assert session.current_wait_type == "COMMAND_RESULT"
    assert session.awaiting_device_command_code == command.command_code
    assert db.added == [command, intent_log, outbox]
    assert db.commit_count == 0
    assert db.rollback_count == 0


def test_device_command_contract_requires_explicit_result_policy() -> None:
    with pytest.raises(ValidationError, match="result_policy"):
        DeviceCommandWriteInput.model_validate(
            {
                "target_device_id": 71,
                "action": "MOVE_FORWARD",
                "payload": {},
            }
        )
    with pytest.raises(ValidationError, match="COMMAND_RESULT"):
        DeviceCommandWriteInput.model_validate(
            {
                "target_device_id": 71,
                "action": "MOVE_FORWARD",
                "payload": {},
                "result_policy": "FIRE_AND_FORGET",
            }
        )


@pytest.mark.asyncio
async def test_runtime_device_command_service_rejects_fire_and_forget_model_bypass() -> None:
    from src.app.device.repositories.device_repository import DeviceRepository
    from src.app.device.services.device_command_service import DeviceCommandService

    class _DeviceRepository(DeviceRepository):
        async def get_runtime_effect_target_for_update(self, _db: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                id=72,
                device_code="CONVEYOR-72",
                work_line_id=41,
                version=2,
                device_status="IDLE",
                maintenance_mode=False,
                current_command_id=None,
                is_active=True,
            )

    session = SimpleNamespace(
        id=31,
        workline_id=41,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        status="RUNNING",
        current_wait_type=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
        awaiting_device_command_code=None,
    )
    with pytest.raises(ValueError, match="COMMAND_RESULT"):
        await DeviceCommandService(device_repository=_DeviceRepository()).prepare_runtime_effect(
            _MutationDb(),  # type: ignore[arg-type]
            request=SimpleNamespace(
                action="MOVE_FORWARD",
                priority=5,
                timeout_ms=30000,
                payload={"business_key": "PKG-1"},
                command_code=None,
                result_policy="FIRE_AND_FORGET",
            ),
            target_device_id=72,
            target_device_code=None,
            expected_workline_id=41,
            expected_fact_version="device:v2",
            expected_available=True,
            session=session,
            workline=SimpleNamespace(id=41, plugin_key="rough_sorter"),
            idempotency_key="system-capability:device.device_command_write@v1:session:31:move-1",
            execution_correlation_id="corr-fire-and-forget",
            trace_id="trace-fire-and-forget",
            intent_log=SimpleNamespace(
                effect_status=RuntimeIntentStatus.PROPOSED,
                dispatch_key="device-command:CMD-FIRE-AND-FORGET",
            ),
        )


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
