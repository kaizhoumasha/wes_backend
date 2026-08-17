from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import (
    DeviceResultReadyFact,
    EvidenceReadyFact,
    FactReference,
    ReconciliationResultReadyFact,
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
from src.app.execution.services.decision_applier import decision_digest
from src.app.execution.services.fact_processor import FactProcessor
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus

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

    async def claim_decision_batch(self, db: object, **kwargs: object) -> list[InboundEvidence]:
        del db
        token = kwargs["claim_token"]
        self.evidence.decision_claim_token = token
        self.evidence.decision_claim_expires_at = kwargs["claim_expires_at"]
        self.evidence.decision_attempt_count += 1
        return [self.evidence]

    async def get_decision_claim_for_update(self, db: object, **kwargs: object) -> InboundEvidence | None:
        del db
        if self.evidence.decision_claim_token != kwargs["claim_token"]:
            return None
        if self.evidence.decision_claim_expires_at < kwargs["now"]:
            return None
        if self.evidence.apply_status != InboundEvidenceApplyStatus.APPLIED or self.evidence.published_at is not None:
            return None
        return self.evidence

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
            status=LineRunEpochStatus.ACTIVE,
            started_at=NOW,
        )

    async def get_by_id_for_update(self, db: object, line_run_epoch_id: int) -> LineRunEpoch | None:
        del db
        return self.epoch if line_run_epoch_id == 11 else None


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
        self.transitions.append(kwargs["target"])
        return execution


class _Correlator:
    async def correlate(self, evidence_id: str) -> InitialExecutionDescriptor:
        assert evidence_id == "31"
        return InitialExecutionDescriptor("TRACE-1", "EXEC-1")


class _RejectingCorrelator:
    def __init__(self) -> None:
        self.calls = 0

    async def correlate(self, evidence_id: str) -> InitialExecutionDescriptor:
        del evidence_id
        self.calls += 1
        raise AssertionError("non-initial evidence must not call correlator")


class _IdentityFactFactory:
    async def build(self, fact: FactReference) -> FactReference:
        return fact


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


@handler(fact_type=EvidenceReadyFact, name="initial", supported_versions=("1.0",))
def _handle_initial(fact: EvidenceReadyFact) -> tuple[Wait, ...]:
    return (Wait(fact.material_execution_id, fact.fact_id, "WAIT_FOR_WMS"),)


@handler(fact_type=DeviceResultReadyFact, name="device_result", supported_versions=("1.0",))
def _handle_device_result(fact: DeviceResultReadyFact) -> tuple[Wait, ...]:
    return (Wait(fact.material_execution_id, fact.fact_id, "NEXT_DEVICE"),)


@handler(fact_type=EvidenceReadyFact, name="failing", supported_versions=("1.0",))
def _failing_handler(fact: EvidenceReadyFact) -> tuple[Wait, ...]:
    del fact
    raise RuntimeError("handler failed")


@handler(fact_type=ReconciliationResultReadyFact, name="reconciliation", supported_versions=("1.0",))
def _handle_reconciliation(fact: ReconciliationResultReadyFact) -> tuple[Wait, ...]:
    return (Wait(fact.material_execution_id, fact.fact_id, "MANUAL_REVIEW"),)


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
    )
    return processor, service, decision_applier


@pytest.mark.asyncio
async def test_initial_evidence_is_correlated_then_decisions_are_published() -> None:
    evidence = _evidence()
    processor, _, applier = _processor(evidence)

    assert await processor.process_batch() == 1

    assert evidence.material_execution_id == 21
    assert evidence.decision_digest == decision_digest(applier.calls[0][1])
    assert evidence.published_at == NOW
    assert evidence.decision_claim_token is None


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
async def test_multi_execution_reconciliation_applies_all_groups_in_one_transaction() -> None:
    evidence = _evidence(
        kind=InboundEvidenceKind.WMS_EVENT,
        operation="inbound.execution.reconciliation_decided@v1",
        operation_id="OP-1",
        normalized_payload={"data": {"reconciliation_id": "REC-1"}},
    )
    evidence_repo = _Evidences(evidence)
    first = MaterialExecution(
        id=21,
        execution_code="EXEC-1",
        material_trace_id="TRACE-1",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.RECONCILING,
        last_transition_reason="UNKNOWN",
        last_transition_evidence_id=31,
        status_changed_at=NOW,
    )
    second = first.model_copy(update={"id": 22, "execution_code": "EXEC-2", "material_trace_id": "TRACE-2"})

    class _BatchExecutions:
        def __init__(self) -> None:
            self.values = {21: first, 22: second}

        async def get_by_id_for_update(self, db, execution_id):  # type: ignore[no-untyped-def]
            del db
            return self.values.get(execution_id)

    class _EvidenceBindings:
        async def list_for_evidence_for_update(self, db, evidence_id):  # type: ignore[no-untyped-def]
            del db
            assert evidence_id == 31
            return [
                SimpleNamespace(material_execution_id=21, ordinal=0),
                SimpleNamespace(material_execution_id=22, ordinal=1),
            ]

    applier = _Applier()
    processor = FactProcessor(
        session_factory=_Sessions(),
        plugin_binding=StaticPluginBinding(
            (
                PluginRuntimeBinding(
                    plugin_key="rough_sorter",
                    plugin_version="1.0.0",
                    handlers=(_handle_reconciliation,),
                    fact_factory=_IdentityFactFactory(),
                ),
            )
        ),
        decision_applier=applier,
        evidence_repository=evidence_repo,
        execution_repository=_BatchExecutions(),
        epoch_repository=_Epochs(),
        evidence_execution_binding_repository=_EvidenceBindings(),
        material_execution_service=_ExecutionService(_Executions()),
        clock=lambda: NOW,
        token_factory=lambda: "claim-1",
    )

    assert await processor.process_batch() == 1

    assert len(applier.calls) == 2
    assert applier.calls[0][0] is applier.calls[1][0]
    assert [call[1][0].material_execution_id for call in applier.calls] == ["EXEC-1", "EXEC-2"]
    assert evidence.published_at == NOW


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
