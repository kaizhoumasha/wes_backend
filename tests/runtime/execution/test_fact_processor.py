from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import (
    CreateWmsConfirmation,
    DeferExecution,
    DeviceResultReadyFact,
    EvidenceReadyFact,
    FactReference,
    PauseForReconciliation,
    RecoveryDecidedFact,
    Wait,
    handler,
)

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.models.material_execution import MaterialExecution, MaterialExecutionStatus
from src.app.execution.plugin_binding import (
    InitialExecutionDescriptor,
    PluginRuntimeBinding,
    StaticPluginBinding,
)
from src.app.execution.services.decision_applier import DecisionApplier, decision_digest
from src.app.execution.services.fact_processor import FactProcessor
from src.app.execution.services.material_execution_service import MaterialExecutionService
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochPositionBinding, LineRunEpochStatus

NOW = datetime(2026, 8, 17, 10, 0, 0)


class _Begin(AbstractAsyncContextManager[object]):
    def __init__(self, db: object) -> None:
        self._db = db

    async def __aenter__(self) -> object:
        return self._db

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Sessions:
    def __init__(self) -> None:
        self.databases: list[object] = []

    def begin(self) -> _Begin:
        db = object()
        self.databases.append(db)
        return _Begin(db)


class _Evidences:
    def __init__(self, evidence: InboundEvidence) -> None:
        self.evidence = evidence
        self.locked_databases: list[object] = []

    async def claim_decision_batch(self, db: object, **kwargs: object) -> list[InboundEvidence]:
        del db
        token = kwargs["claim_token"]
        self.evidence.decision_claim_token = token
        self.evidence.decision_claim_expires_at = kwargs["claim_expires_at"]
        return [self.evidence]

    async def get_decision_claim_for_update(self, db: object, **kwargs: object) -> InboundEvidence | None:
        self.locked_databases.append(db)
        if self.evidence.decision_claim_token != kwargs["claim_token"]:
            return None
        if self.evidence.decision_claim_expires_at < kwargs["now"]:
            return None
        if self.evidence.apply_status != InboundEvidenceApplyStatus.APPLIED or self.evidence.published_at is not None:
            return None
        return self.evidence

    async def get_by_id_for_update(self, db: object, evidence_id: int) -> InboundEvidence | None:
        del db
        return self.evidence if self.evidence.id == evidence_id else None

    async def flush(self, db: object) -> None:
        del db


class _Epochs:
    def __init__(self) -> None:
        self.epoch = LineRunEpoch(
            id=11,
            epoch_code="EPOCH-1",
            workline_id=7,
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="AUTO",
            topology_digest="a" * 64,
            configuration_digest="b" * 64,
            configuration_snapshot_json={},
            status=LineRunEpochStatus.ACTIVE,
            started_at=NOW,
        )

    async def get_by_id_for_update(self, db: object, line_run_epoch_id: int) -> LineRunEpoch | None:
        del db
        return self.epoch if line_run_epoch_id == 11 else None

    async def list_position_bindings(self, db: object, line_run_epoch_id: int) -> list[LineRunEpochPositionBinding]:
        del db
        if line_run_epoch_id != 11:
            return []
        return [
            LineRunEpochPositionBinding(
                line_run_epoch_id=11,
                position_role="PIPELINE_OUTLET",
                location_id="LINE-OUT",
                location_type="PIPELINE_OUTLET",
            )
        ]


class _Executions:
    def __init__(self) -> None:
        self.execution: MaterialExecution | None = None

    async def get_by_id_for_update(self, db: object, execution_id: int) -> MaterialExecution | None:
        del db
        return self.execution if self.execution is not None and self.execution.id == execution_id else None

    async def get_by_execution_code_for_update(self, db: object, execution_code: str) -> MaterialExecution | None:
        del db
        return (
            self.execution if self.execution is not None and self.execution.execution_code == execution_code else None
        )


class _ExecutionFlushRepository:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self, db: object) -> None:
        del db
        self.flush_count += 1


class _ExecutionService:
    def __init__(self, executions: _Executions) -> None:
        self._executions = executions
        self.transitions: list[MaterialExecutionStatus] = []

    async def create_or_get_for_initial_evidence(self, db: object, **kwargs: object) -> MaterialExecution:
        del db
        if self._executions.execution is None:
            self._executions.execution = MaterialExecution(
                id=21,
                execution_code=kwargs["execution_code"],
                material_trace_id=kwargs["material_trace_id"],
                workline_id=kwargs["workline_id"],
                line_run_epoch_id=kwargs["line_run_epoch_id"],
                status=MaterialExecutionStatus.CREATED,
                last_transition_reason="INITIAL_EVIDENCE",
                last_transition_evidence_id=kwargs["evidence_id"],
                status_changed_at=kwargs["changed_at"],
            )
        return self._executions.execution

    async def transition(self, db: object, execution: MaterialExecution, **kwargs: object) -> MaterialExecution:
        del db
        execution.status = kwargs["target"]
        execution.last_transition_reason = kwargs["reason_code"]
        execution.last_transition_evidence_id = kwargs["evidence_id"]
        self.transitions.append(kwargs["target"])
        return execution


class _Correlator:
    def __init__(self) -> None:
        self.databases: list[object] = []

    async def correlate(self, db: object, evidence_id: str) -> InitialExecutionDescriptor:
        self.databases.append(db)
        assert evidence_id == "31"
        return InitialExecutionDescriptor("TRACE-1", "EXEC-1")


class _RejectingCorrelator:
    def __init__(self) -> None:
        self.calls = 0

    async def correlate(self, db: object, evidence_id: str) -> InitialExecutionDescriptor:
        del db, evidence_id
        self.calls += 1
        raise AssertionError("non-initial evidence must not call correlator")


class _IdentityFactFactory:
    async def build(self, _db: object, fact: FactReference) -> FactReference:
        return fact


@dataclass(frozen=True, slots=True)
class _ChangingFact(EvidenceReadyFact):
    decision: str


class _ChangingFactFactory:
    def __init__(self, decisions: tuple[str, ...]) -> None:
        self._decisions = iter(decisions)
        self.calls = 0

    async def build(self, db: object, fact: FactReference) -> FactReference:
        del db
        self.calls += 1
        return _ChangingFact(
            fact_id=fact.fact_id,
            evidence_id=fact.evidence_id,
            fact_version=fact.fact_version,
            material_execution_id=fact.material_execution_id,
            decision=next(self._decisions),
        )


@handler(fact_type=_ChangingFact, name="changing", supported_versions=("1.0",))
def _handle_changing(fact: _ChangingFact) -> tuple[DeferExecution | PauseForReconciliation | Wait, ...]:
    if fact.decision == "DEFER":
        return (DeferExecution(fact.material_execution_id, fact.fact_id, "SNAPSHOT_NOT_READY"),)
    if fact.decision == "PAUSE":
        return (
            PauseForReconciliation(
                fact.material_execution_id,
                fact.fact_id,
                "SNAPSHOT_CONFLICT",
                ("snapshot:resource",),
            ),
        )
    return (Wait(fact.material_execution_id, fact.fact_id, "SNAPSHOT_READY"),)


class _Applier:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[object, tuple[object, ...]]] = []
        self.error = error

    async def apply(
        self,
        db: object,
        evidence: object,
        execution: object,
        fact: object,
        decisions: tuple[object, ...],
    ) -> str:
        del evidence, execution, fact
        self.calls.append((db, decisions))
        if self.error is not None:
            raise self.error
        return decision_digest(decisions)


class _PersistingEvidences(_Evidences):
    def __init__(self, evidence: InboundEvidence) -> None:
        super().__init__(evidence)
        self.persisted_decision_digest = evidence.decision_digest

    async def flush(self, db: object) -> None:
        del db
        self.persisted_decision_digest = self.evidence.decision_digest


class _PopulateExistingApplier(_Applier):
    def __init__(self, evidences: _PersistingEvidences) -> None:
        super().__init__()
        self._evidences = evidences

    async def apply(
        self,
        db: object,
        evidence: object,
        execution: object,
        fact: object,
        decisions: tuple[object, ...],
    ) -> str:
        assert isinstance(evidence, InboundEvidence)
        evidence.decision_digest = self._evidences.persisted_decision_digest
        return await super().apply(db, evidence, execution, fact, decisions)


@handler(fact_type=EvidenceReadyFact, name="initial", supported_versions=("1.0",))
def _handle_initial(fact: EvidenceReadyFact) -> tuple[Wait, ...]:
    return (Wait(fact.material_execution_id, fact.fact_id, "WAIT_FOR_WMS"),)


@handler(fact_type=EvidenceReadyFact, name="wms", supported_versions=("1.0",))
def _handle_wms(fact: EvidenceReadyFact) -> tuple[CreateWmsConfirmation, ...]:
    return (
        CreateWmsConfirmation(
            fact.material_execution_id,
            fact.fact_id,
            "inbound.material.admission_decide@v1",
            "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
            (fact.evidence_id,),
            (f"execution:{fact.material_execution_id}",),
        ),
    )


class _TaskQueue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.wms_wakes = 0
        self.error = error

    def enqueue_wms_confirmations(self) -> None:
        self.wms_wakes += 1
        if self.error is not None:
            raise self.error


@handler(fact_type=DeviceResultReadyFact, name="device_result", supported_versions=("1.0",))
def _handle_device_result(fact: DeviceResultReadyFact) -> tuple[Wait, ...]:
    return (Wait(fact.material_execution_id, fact.fact_id, "NEXT_DEVICE"),)


@handler(fact_type=EvidenceReadyFact, name="failing", supported_versions=("1.0",))
def _failing_handler(fact: EvidenceReadyFact) -> tuple[Wait, ...]:
    del fact
    raise RuntimeError("handler failed")


@handler(fact_type=EvidenceReadyFact, name="defer", supported_versions=("1.0",))
def _defer_handler(fact: EvidenceReadyFact) -> tuple[DeferExecution, ...]:
    return (DeferExecution(fact.material_execution_id, fact.fact_id, "DEVICE_BUSY"),)


@handler(fact_type=EvidenceReadyFact, name="mixed-defer", supported_versions=("1.0",))
def _mixed_defer_handler(fact: EvidenceReadyFact) -> tuple[DeferExecution | Wait, ...]:
    return (
        DeferExecution(fact.material_execution_id, fact.fact_id, "DEVICE_BUSY"),
        Wait(fact.material_execution_id, fact.fact_id, "MUST_NOT_MIX"),
    )


@handler(fact_type=RecoveryDecidedFact, name="recovery", supported_versions=("1.0",))
def _handle_recovery(fact: RecoveryDecidedFact) -> tuple[Wait, ...]:
    return (Wait(fact.material_execution_id, fact.fact_id, "RECOVERY_ABORTED"),)


_RECOVERY_CONTINUATION_READY = False


@handler(fact_type=RecoveryDecidedFact, name="recovery-continuation", supported_versions=("1.0",))
def _handle_recovery_continuation(fact: RecoveryDecidedFact) -> tuple[DeferExecution | Wait, ...]:
    if not _RECOVERY_CONTINUATION_READY:
        return (DeferExecution(fact.material_execution_id, fact.fact_id, "RECOVERY_CONTINUATION_NOT_READY"),)
    return (Wait(fact.material_execution_id, fact.fact_id, "RECOVERY_CONTINUED"),)


def _evidence(**changes: object) -> InboundEvidence:
    values: dict[str, object] = {
        "id": 31,
        "kind": InboundEvidenceKind.DEVICE_EVENT,
        "source_identity": "SCAN-1",
        "payload_digest": "c" * 64,
        "normalized_payload": {"data": {}},
        "received_at": NOW,
        "line_run_epoch_id": 11,
        "contract_version": "1.0",
        "apply_status": InboundEvidenceApplyStatus.APPLIED,
    }
    values.update(changes)
    return InboundEvidence(**values)


def _recovery_continuation_processor(
    *, execution: MaterialExecution | None = None
) -> tuple[FactProcessor, InboundEvidence, MaterialExecution, _Applier]:
    evidence = _evidence(
        kind=InboundEvidenceKind.WMS_EVENT,
        operation="inbound.execution.recovery_decided@v1",
        operation_id="OP-RECOVERY-CONTINUATION",
        material_execution_id=21,
        normalized_payload={
            "data": {
                "recovery_id": "REC-CONTINUATION",
                "material_execution_id": "EXEC-1",
                "material_trace_id": "TRACE-1",
                "reconciling_evidence_id": "30",
                "decision": "CONTINUE",
                "authoritative_position": {"type": "HANDOFF_POSITION", "location_code": "LINE-OUT"},
                "reason_code": "POSITION_CONFIRMED",
            }
        },
    )
    executions = _Executions()
    execution = execution or MaterialExecution(
        id=21,
        execution_code="EXEC-1",
        material_trace_id="TRACE-1",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.RECONCILING,
        last_transition_reason="TRANSPORT_UNKNOWN",
        last_transition_evidence_id=30,
        status_changed_at=NOW,
    )
    executions.execution = execution
    applier = _Applier()
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=_plugins(_handle_recovery_continuation),
        decision_applier=applier,
        evidence_repository=_Evidences(evidence),
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=_ExecutionService(executions),
        clock=lambda: NOW,
        token_factory=lambda: "claim-recovery-continuation",
    )
    return processor, evidence, execution, applier


def _plugins(target: object = _handle_initial, *, correlator: object | None = _Correlator()) -> StaticPluginBinding:
    return StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key="rough_sorter",
                plugin_version="1.0.0",
                handlers=(target,),
                fact_factory=_IdentityFactFactory(),
                initial_execution_correlator=correlator,
            ),
        )
    )


def _processor(
    evidence: InboundEvidence,
    *,
    target: object = _handle_initial,
    correlator: object | None = _Correlator(),
    applier: _Applier | None = None,
    task_queue: _TaskQueue | None = None,
) -> tuple[FactProcessor, _ExecutionService, _Applier]:
    executions = _Executions()
    service = _ExecutionService(executions)
    decision_applier = applier or _Applier()
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=_plugins(target, correlator=correlator),
        decision_applier=decision_applier,
        evidence_repository=_Evidences(evidence),
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=service,
        clock=lambda: NOW,
        token_factory=lambda: "claim-1",
        task_queue_gateway=task_queue,  # type: ignore[arg-type]
    )
    return processor, service, decision_applier


def _changing_processor(
    decisions: tuple[str, str],
) -> tuple[FactProcessor, InboundEvidence, _Executions, _ExecutionService, DecisionApplier, _ChangingFactFactory]:
    evidence = _evidence()
    executions = _Executions()
    factory = _ChangingFactFactory(decisions)
    binding = StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key="rough_sorter",
                plugin_version="1.0.0",
                handlers=(_handle_changing,),
                fact_factory=factory,
                initial_execution_correlator=_Correlator(),
            ),
        )
    )
    service = _ExecutionService(executions)
    applier = DecisionApplier(
        epoch_repository=object(),  # type: ignore[arg-type]
        device_command_service=object(),  # type: ignore[arg-type]
        wms_confirmation_service=object(),  # type: ignore[arg-type]
        wms_request_resolver=object(),  # type: ignore[arg-type]
        rack_binding_repository=object(),  # type: ignore[arg-type]
        transport_service=object(),  # type: ignore[arg-type]
        material_execution_service=service,
        clock=lambda: NOW,
    )
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=binding,
        decision_applier=applier,
        evidence_repository=_Evidences(evidence),
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=service,
        clock=lambda: NOW,
        token_factory=lambda: "claim-changing",
    )
    return processor, evidence, executions, service, applier, factory


@pytest.mark.asyncio
async def test_committed_immediate_confirmation_wakes_wms_dispatcher_without_payload() -> None:
    evidence = _evidence()
    queue = _TaskQueue()
    processor, _, _ = _processor(evidence, target=_handle_wms, task_queue=queue)

    assert await processor.process_batch() == 1

    assert evidence.published_at == NOW
    assert queue.wms_wakes == 1


@pytest.mark.asyncio
async def test_wms_wake_failure_does_not_rollback_applied_decisions() -> None:
    evidence = _evidence()
    queue = _TaskQueue(error=RuntimeError("queue unavailable"))
    processor, _, applier = _processor(evidence, target=_handle_wms, task_queue=queue)

    assert await processor.process_batch() == 1

    assert evidence.published_at == NOW
    assert len(applier.calls) == 1
    assert queue.wms_wakes == 1


@pytest.mark.asyncio
async def test_initial_evidence_is_correlated_then_decisions_are_published() -> None:
    evidence = _evidence()
    correlator = _Correlator()
    processor, _, applier = _processor(evidence, correlator=correlator)

    assert await processor.process_batch() == 1

    assert evidence.material_execution_id == 21
    assert evidence.decision_digest == decision_digest(applier.calls[0][1])
    assert evidence.published_at == NOW
    assert evidence.decision_claim_token is None
    assert correlator.databases == [processor._evidences.locked_databases[0]]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_decision_digest_is_flushed_before_populate_existing_can_reload_the_evidence() -> None:
    evidence = _evidence()
    evidences = _PersistingEvidences(evidence)
    executions = _Executions()
    service = _ExecutionService(executions)
    applier = _PopulateExistingApplier(evidences)
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=_plugins(_handle_wms),
        decision_applier=applier,
        evidence_repository=evidences,
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=service,
        clock=lambda: NOW,
        token_factory=lambda: "claim-autoflush-disabled",
    )

    assert await processor.process_batch() == 1

    assert evidence.published_at == NOW
    assert evidence.decision_digest == decision_digest(applier.calls[0][1])


@pytest.mark.asyncio
async def test_same_evidence_with_different_decision_digest_enters_reconciliation_without_effects() -> None:
    evidence = _evidence(decision_digest="d" * 64)
    processor, execution_service, applier = _processor(evidence)

    assert await processor.process_batch() == 1

    assert evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING
    assert execution_service.transitions == [MaterialExecutionStatus.RECONCILING]
    assert applier.calls == []


@pytest.mark.asyncio
async def test_handler_failure_releases_claim_with_bounded_backoff() -> None:
    evidence = _evidence()
    processor, _, applier = _processor(evidence, target=_failing_handler)

    assert await processor.process_batch() == 0

    assert applier.calls == []
    assert evidence.decision_claim_token is None
    assert evidence.decision_next_attempt_at == datetime(2026, 8, 17, 10, 0, 1)
    assert evidence.published_at is None
    assert evidence.decision_attempt_count == 1


@pytest.mark.asyncio
async def test_single_defer_releases_claim_holds_execution_without_publishing_or_attempt() -> None:
    evidence = _evidence()
    processor, execution_service, applier = _processor(evidence, target=_defer_handler)

    assert await processor.process_batch() == 1

    assert evidence.published_at is None
    assert evidence.decision_digest is None
    assert evidence.decision_attempt_count == 0
    assert evidence.decision_next_attempt_at == NOW
    assert evidence.decision_claim_token is None
    assert evidence.decision_claim_expires_at is None
    assert execution_service.transitions == [MaterialExecutionStatus.HOLD]
    assert applier.calls == []


@pytest.mark.asyncio
async def test_mixed_defer_fails_closed_as_a_real_handler_failure() -> None:
    evidence = _evidence()
    processor, _, applier = _processor(evidence, target=_mixed_defer_handler)

    assert await processor.process_batch() == 0

    assert evidence.published_at is None
    assert evidence.decision_digest is None
    assert evidence.decision_attempt_count == 1
    assert evidence.decision_next_attempt_at == datetime(2026, 8, 17, 10, 0, 1)
    assert applier.calls == []


@pytest.mark.asyncio
async def test_lock_free_action_to_locked_defer_uses_current_decision_without_attempt() -> None:
    processor, evidence, _, service, _, factory = _changing_processor(("WAIT", "DEFER"))

    assert await processor.process_batch() == 1

    assert factory.calls == 2
    assert service.transitions == [MaterialExecutionStatus.HOLD]
    assert evidence.published_at is None
    assert evidence.decision_digest is None
    assert evidence.decision_next_attempt_at == NOW
    assert evidence.decision_attempt_count == 0


@pytest.mark.asyncio
async def test_lock_free_defer_to_locked_action_uses_current_decision_without_attempt() -> None:
    processor, evidence, executions, service, _, factory = _changing_processor(("DEFER", "PAUSE"))

    assert await processor.process_batch() == 1

    assert factory.calls == 2
    assert executions.execution is not None
    assert MaterialExecutionStatus(executions.execution.status) is MaterialExecutionStatus.RECONCILING
    assert executions.execution.last_transition_evidence_id == evidence.id
    assert service.transitions == [MaterialExecutionStatus.RECONCILING]
    assert evidence.published_at == NOW
    assert evidence.decision_digest is not None
    assert evidence.decision_attempt_count == 0


@pytest.mark.asyncio
async def test_missing_initial_correlator_fails_closed_without_guessing_execution() -> None:
    evidence = _evidence()
    processor, _, _ = _processor(evidence, correlator=None)

    assert await processor.process_batch() == 0

    assert evidence.material_execution_id is None
    assert evidence.decision_next_attempt_at == datetime(2026, 8, 17, 10, 0, 1)


@pytest.mark.asyncio
async def test_device_result_uses_command_frozen_execution_without_calling_initial_correlator() -> None:
    evidence = _evidence(
        kind=InboundEvidenceKind.DEVICE_RESULT,
        material_execution_id=21,
        device_code="ARM-1",
        command_code="CMD-1",
    )
    executions = _Executions()
    executions.execution = MaterialExecution(
        id=21,
        execution_code="EXEC-1",
        material_trace_id="TRACE-1",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.RUNNING,
        last_transition_reason="COMMAND_CREATED",
        last_transition_evidence_id=30,
        status_changed_at=NOW,
    )
    correlator = _RejectingCorrelator()
    applier = _Applier()
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=_plugins(_handle_device_result, correlator=correlator),
        decision_applier=applier,
        evidence_repository=_Evidences(evidence),
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=_ExecutionService(executions),
        clock=lambda: NOW,
        token_factory=lambda: "claim-1",
    )

    assert await processor.process_batch() == 1
    assert correlator.calls == 0
    assert applier.calls[0][1][0].material_execution_id == "EXEC-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [InboundEvidenceKind.DEVICE_RESULT, InboundEvidenceKind.WMS_RESULT])
async def test_non_initial_evidence_without_execution_fails_closed_without_correlator(
    kind: InboundEvidenceKind,
) -> None:
    evidence = _evidence(
        kind=kind,
        device_code="ARM-1" if kind is InboundEvidenceKind.DEVICE_RESULT else None,
        command_code="CMD-1" if kind is InboundEvidenceKind.DEVICE_RESULT else None,
        operation="op" if kind is InboundEvidenceKind.WMS_RESULT else None,
        operation_id="OP-1" if kind is InboundEvidenceKind.WMS_RESULT else None,
    )
    correlator = _RejectingCorrelator()
    processor, _, _ = _processor(evidence, correlator=correlator)

    assert await processor.process_batch() == 0
    assert correlator.calls == 0
    assert evidence.material_execution_id is None
    assert evidence.decision_next_attempt_at == datetime(2026, 8, 17, 10, 0, 1)


@pytest.mark.asyncio
async def test_exhausted_application_attempt_enters_reconciliation() -> None:
    evidence = _evidence(decision_attempt_count=4)
    processor, _, _ = _processor(evidence, target=_failing_handler)

    assert await processor.process_batch() == 0

    assert evidence.decision_attempt_count == 5
    assert evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING
    assert evidence.decision_next_attempt_at is None


@pytest.mark.asyncio
async def test_exhausted_application_keeps_closed_execution_terminal_and_reconciles_evidence() -> None:
    evidence = _evidence(material_execution_id=21, decision_attempt_count=4)
    executions = _Executions()
    executions.execution = MaterialExecution(
        id=21,
        execution_code="EXEC-1",
        material_trace_id="TRACE-1",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.CLOSED,
        last_transition_reason="PLACEMENT_RECORDED",
        last_transition_evidence_id=30,
        status_changed_at=NOW,
        closed_at=NOW,
    )
    execution_service = _ExecutionService(executions)
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=_plugins(target=_failing_handler),
        decision_applier=_Applier(),
        evidence_repository=_Evidences(evidence),
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=execution_service,
        clock=lambda: NOW,
        token_factory=lambda: "claim-1",
    )

    assert await processor.process_batch() == 0
    assert evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING
    assert evidence.decision_claim_token is None
    assert evidence.decision_next_attempt_at is None
    assert MaterialExecutionStatus(executions.execution.status) is MaterialExecutionStatus.CLOSED
    assert execution_service.transitions == []


@pytest.mark.asyncio
async def test_digest_conflict_keeps_closed_execution_terminal_and_reconciles_evidence() -> None:
    evidence = _evidence(material_execution_id=21, decision_digest="d" * 64)
    executions = _Executions()
    executions.execution = MaterialExecution(
        id=21,
        execution_code="EXEC-1",
        material_trace_id="TRACE-1",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.CLOSED,
        last_transition_reason="PLACEMENT_RECORDED",
        last_transition_evidence_id=30,
        status_changed_at=NOW,
        closed_at=NOW,
    )
    execution_service = _ExecutionService(executions)
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=_plugins(),
        decision_applier=_Applier(),
        evidence_repository=_Evidences(evidence),
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=execution_service,
        clock=lambda: NOW,
        token_factory=lambda: "claim-1",
    )

    assert await processor.process_batch() == 1
    assert evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING
    assert evidence.decision_claim_token is None
    assert evidence.decision_next_attempt_at is None
    assert MaterialExecutionStatus(executions.execution.status) is MaterialExecutionStatus.CLOSED
    assert execution_service.transitions == []


@pytest.mark.asyncio
async def test_recovery_fact_targets_exactly_one_execution() -> None:
    evidence = _evidence(
        kind=InboundEvidenceKind.WMS_EVENT,
        operation="inbound.execution.recovery_decided@v1",
        operation_id="OP-1",
        material_execution_id=21,
        normalized_payload={
            "data": {
                "recovery_id": "REC-1",
                "material_execution_id": "EXEC-1",
                "material_trace_id": "TRACE-1",
                "reconciling_evidence_id": "30",
                "decision": "ABORT",
                "authoritative_position": None,
                "reason_code": "MATERIAL_CONFIRMED_MISSING",
            }
        },
    )
    evidence_repo = _Evidences(evidence)
    executions = _Executions()
    executions.execution = MaterialExecution(
        id=21,
        execution_code="EXEC-1",
        material_trace_id="TRACE-1",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.RECONCILING,
        last_transition_reason="UNKNOWN",
        last_transition_evidence_id=30,
        status_changed_at=NOW,
    )

    applier = _Applier()
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=StaticPluginBinding(
            (
                PluginRuntimeBinding(
                    plugin_key="rough_sorter",
                    plugin_version="1.0.0",
                    handlers=(_handle_recovery,),
                    fact_factory=_IdentityFactFactory(),
                ),
            )
        ),
        decision_applier=applier,
        evidence_repository=evidence_repo,
        execution_repository=executions,
        epoch_repository=_Epochs(),
        material_execution_service=_ExecutionService(executions),
        clock=lambda: NOW,
        token_factory=lambda: "claim-1",
    )

    assert await processor.process_batch() == 1
    assert len(applier.calls) == 1
    assert evidence.published_at == NOW


@pytest.mark.asyncio
async def test_recovery_defer_rebuilds_and_applies_the_same_evidence_once_when_ready() -> None:
    global _RECOVERY_CONTINUATION_READY
    _RECOVERY_CONTINUATION_READY = False
    processor, evidence, execution, applier = _recovery_continuation_processor()
    try:
        assert await processor.process_batch() == 1
        assert MaterialExecutionStatus(execution.status) is MaterialExecutionStatus.HOLD
        assert execution.last_transition_evidence_id == evidence.id
        assert evidence.published_at is None
        assert evidence.decision_digest is None
        assert evidence.decision_attempt_count == 0

        _RECOVERY_CONTINUATION_READY = True
        assert await processor.process_batch() == 1
        assert evidence.published_at == NOW
        assert len(applier.calls) == 1
        assert await processor.process_batch() == 0
        assert len(applier.calls) == 1
    finally:
        _RECOVERY_CONTINUATION_READY = False


@pytest.mark.asyncio
async def test_recovery_defer_does_not_cross_a_new_last_transition_conflict() -> None:
    global _RECOVERY_CONTINUATION_READY
    _RECOVERY_CONTINUATION_READY = False
    processor, evidence, execution, applier = _recovery_continuation_processor()
    try:
        assert await processor.process_batch() == 1
        execution.last_transition_evidence_id = 32

        _RECOVERY_CONTINUATION_READY = True
        assert await processor.process_batch() == 0
        assert evidence.published_at is None
        assert evidence.decision_digest is None
        assert evidence.decision_attempt_count == 1
        assert applier.calls == []
    finally:
        _RECOVERY_CONTINUATION_READY = False


@pytest.mark.asyncio
async def test_determinate_pause_refreshes_reconciling_fence_before_late_recovery_is_applied() -> None:
    global _RECOVERY_CONTINUATION_READY
    _RECOVERY_CONTINUATION_READY = True
    processor, recovery_evidence, execution, recovery_applier = _recovery_continuation_processor()
    determinate_evidence = _evidence(
        id=32,
        kind=InboundEvidenceKind.TRANSPORT_RESULT,
        source_identity="transport:T-1:outcome:2",
        material_execution_id=21,
    )
    fact = FactReference("transport:T-1:outcome:2", "32", "1.0", "EXEC-1")
    decision = PauseForReconciliation("EXEC-1", fact.fact_id, "DETERMINATE_MISMATCH", ("rack-new",))
    repository = _ExecutionFlushRepository()
    transition_times = iter((NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)))
    pause_applier = DecisionApplier(
        epoch_repository=object(),
        device_command_service=object(),
        wms_confirmation_service=object(),
        wms_request_resolver=object(),
        rack_binding_repository=object(),
        transport_service=object(),
        material_execution_service=MaterialExecutionService(repository),
        clock=lambda: next(transition_times),
    )
    try:
        await pause_applier.apply(object(), determinate_evidence, execution, fact, (decision,))
        await pause_applier.apply(object(), determinate_evidence, execution, fact, (decision,))

        assert await processor.process_batch() == 0
        assert MaterialExecutionStatus(execution.status) is MaterialExecutionStatus.RECONCILING
        assert execution.last_transition_reason == "DETERMINATE_MISMATCH"
        assert execution.last_transition_evidence_id == 32
        assert execution.status_changed_at == NOW + timedelta(seconds=1)
        assert repository.flush_count == 2
        assert recovery_evidence.published_at is None
        assert recovery_evidence.decision_attempt_count == 1
        assert recovery_applier.calls == []
    finally:
        _RECOVERY_CONTINUATION_READY = False


@pytest.mark.asyncio
async def test_processor_rejects_unbounded_batches() -> None:
    processor, _, _ = _processor(_evidence())

    with pytest.raises(ValueError, match="between 1 and 100"):
        await processor.process_batch(101)


@pytest.mark.asyncio
async def test_failure_recording_error_does_not_abort_remaining_claimed_evidence() -> None:
    first = _evidence(id=31)
    second = _evidence(id=32, source_identity="SCAN-2")

    class _ClaimOnlyRepository:
        async def claim_decision_batch(self, db, **kwargs):  # type: ignore[no-untyped-def]
            del db, kwargs
            return [first, second]

    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=_plugins(),
        decision_applier=_Applier(),
        evidence_repository=_ClaimOnlyRepository(),
        execution_repository=_Executions(),
        epoch_repository=_Epochs(),
        material_execution_service=_ExecutionService(_Executions()),
        clock=lambda: NOW,
        token_factory=lambda: "claim-1",
    )
    processor._prepare_fact = AsyncMock(side_effect=[RuntimeError("first"), RuntimeError("second")])
    processor._record_failure = AsyncMock(side_effect=[RuntimeError("record failed"), None])

    assert await processor.process_batch() == 0
    assert processor._prepare_fact.await_count == 2
    assert processor._record_failure.await_count == 2
