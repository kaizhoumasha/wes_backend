"""有界领取已验证 evidence，调用纯 handler 并原子应用封闭 Decision。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from wes_plugin_sdk import DeferExecution, FactReference
from wes_plugin_sdk.wms_types import InboundWmsIntent

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
)
from src.app.execution.models.material_execution import MaterialExecutionStatus
from src.app.execution.repositories import inbound_evidence_repository, material_execution_repository
from src.app.execution.services.decision_applier import DecisionApplier, decision_digest
from src.app.execution.services.fact_builder import FactBuilder
from src.app.execution.services.material_execution_service import MaterialExecutionService
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncContextManager, Callable

    from src.app.execution.plugin_binding import StaticPluginBinding
    from src.app.workline.models.workline import WorkLine
    from src.core.task_queue_gateway import TaskQueueGateway

logger = logging.getLogger(__name__)

_CLAIM_SECONDS = 30
_MAX_ATTEMPTS = 5
_MAX_BATCH_SIZE = 100
_MAX_BACKOFF_SECONDS = 300


class EvidenceRepositoryPort(Protocol):
    async def claim_decision_batch(self, db: object, **kwargs: object) -> list[InboundEvidence]: ...

    async def get_decision_claim_for_update(self, db: object, **kwargs: object) -> InboundEvidence | None: ...

    async def get_by_id_for_update(self, db: object, evidence_id: int) -> InboundEvidence | None: ...

    async def flush(self, db: object) -> None: ...


class ExecutionRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: object, execution_id: int) -> MaterialExecution | None: ...

    async def get_by_execution_code_for_update(
        self,
        db: object,
        execution_code: str,
    ) -> MaterialExecution | None: ...


class WorkLineRepositoryPort(Protocol):
    async def get_for_update(self, db: object, workline_id: int) -> WorkLine | None: ...

    async def list_position_bindings(self, db: object, workline_id: int) -> list[object]: ...


class InitialExecutionServicePort(Protocol):
    async def create_or_get_for_initial_evidence(self, db: object, **kwargs: object) -> MaterialExecution: ...

    async def transition(self, db: object, execution: MaterialExecution, **kwargs: object) -> MaterialExecution: ...


class SessionFactoryPort(Protocol):
    def begin(self) -> AsyncContextManager[object]: ...


@dataclass(frozen=True, slots=True)
class _PreparedFact:
    fact: FactReference
    plugin_key: str
    plugin_version: str
    execution: MaterialExecution


class FactProcessor:
    def __init__(
        self,
        *,
        session_factory: SessionFactoryPort,
        plugin_binding: StaticPluginBinding,
        decision_applier: DecisionApplier,
        evidence_repository: EvidenceRepositoryPort | None = None,
        execution_repository: ExecutionRepositoryPort | None = None,
        workline_repository: WorkLineRepositoryPort | None = None,
        material_execution_service: InitialExecutionServicePort | None = None,
        fact_builder: FactBuilder | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
        token_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        task_queue_gateway: TaskQueueGateway | None = None,
    ) -> None:
        self._sessions = session_factory
        self._plugins = plugin_binding
        self._applier = decision_applier
        self._evidences = evidence_repository or cast("EvidenceRepositoryPort", inbound_evidence_repository)
        self._executions = execution_repository or cast("ExecutionRepositoryPort", material_execution_repository)
        self._worklines = workline_repository or cast("WorkLineRepositoryPort", WorkLineRepository())
        self._execution_service = material_execution_service or cast(
            "InitialExecutionServicePort", MaterialExecutionService()
        )
        self._fact_builder = fact_builder or FactBuilder()
        self._clock = clock
        self._token_factory = token_factory
        self._task_queue = task_queue_gateway

    async def process_batch(self, limit: int = _MAX_BATCH_SIZE) -> int:
        if not 1 <= limit <= _MAX_BATCH_SIZE:
            raise ValueError("limit must be between 1 and 100")
        now = self._clock()
        token = self._token_factory()
        async with self._sessions.begin() as db:
            claimed = await self._evidences.claim_decision_batch(
                db,
                now=now,
                claim_token=token,
                claim_expires_at=now + timedelta(seconds=_CLAIM_SECONDS),
                limit=limit,
            )
            evidence_ids = [cast("int", evidence.id) for evidence in claimed if evidence.id is not None]

        processed = 0
        for evidence_id in evidence_ids:
            try:
                prepared = await self._prepare_fact(evidence_id, token)
                _ = self._decision_groups(prepared)
                if await self._apply_locked(evidence_id, token):
                    self._enqueue_wms_confirmations()
                processed += 1
            except Exception:  # worker 必须隔离单条 evidence，并通过持久状态有界恢复。
                logger.exception("execution.fact_processing_failed", extra={"evidence_id": evidence_id})
                try:
                    await self._record_failure(evidence_id, token)
                except Exception:
                    logger.exception("execution.fact_failure_recording_failed", extra={"evidence_id": evidence_id})
        return processed

    def _enqueue_wms_confirmations(self) -> None:
        if self._task_queue is None:
            return
        try:
            self._task_queue.enqueue_wms_confirmations()
        except Exception:
            logger.exception("execution.wms_confirmation_wake_failed", extra={"event": "wms_confirmation_wake_failed"})

    async def _prepare_fact(self, evidence_id: int, token: str) -> tuple[_PreparedFact, ...]:
        now = self._clock()
        async with self._sessions.begin() as db:
            evidence = await self._claimed(db, evidence_id, token, now)
            return await self._prepare_facts_in_session(db, evidence, now)

    async def _apply_locked(self, evidence_id: int, token: str) -> bool:
        now = self._clock()
        async with self._sessions.begin() as db:
            evidence = await self._claimed(db, evidence_id, token, now)
            prepared = await self._prepare_facts_in_session(db, evidence, now)
            current_groups = self._decision_groups(prepared)
            defer = self._single_defer(current_groups)
            if defer is not None:
                await self._hold_deferred(db, evidence, prepared, defer, now)
                return False
            current_decisions = tuple(decision for group in current_groups for decision in group)
            digest = decision_digest(current_decisions)
            if evidence.decision_digest is None:
                evidence.decision_digest = digest
                await self._evidences.flush(db)
            elif evidence.decision_digest != digest:
                evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
                evidence.decision_claim_token = None
                evidence.decision_claim_expires_at = None
                evidence.decision_next_attempt_at = None
                await self._transition_all_executions(db, evidence, now, reason_code="DECISION_DIGEST_CONFLICT")
                await self._evidences.flush(db)
                return False
            for item, group in zip(prepared, current_groups, strict=True):
                _ = await self._applier.apply(db, evidence, item.execution, item.fact, group)
            evidence.published_at = now
            evidence.decision_claim_token = None
            evidence.decision_claim_expires_at = None
            evidence.decision_next_attempt_at = None
            await self._evidences.flush(db)
            return any(isinstance(decision, InboundWmsIntent) for decision in current_decisions)

    async def _record_failure(self, evidence_id: int, token: str) -> None:
        now = self._clock()
        async with self._sessions.begin() as db:
            evidence = await self._evidences.get_decision_claim_for_update(
                db,
                evidence_id=evidence_id,
                claim_token=token,
                now=now,
            )
            if evidence is None:
                return
            evidence.decision_claim_token = None
            evidence.decision_claim_expires_at = None
            evidence.decision_attempt_count += 1
            if evidence.decision_attempt_count >= _MAX_ATTEMPTS:
                evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
                evidence.decision_next_attempt_at = None
                await self._transition_all_executions(db, evidence, now, reason_code="DECISION_APPLICATION_EXHAUSTED")
            else:
                backoff_seconds = min(2 ** max(evidence.decision_attempt_count - 1, 0), _MAX_BACKOFF_SECONDS)
                evidence.decision_next_attempt_at = now + timedelta(seconds=backoff_seconds)
            await self._evidences.flush(db)

    @staticmethod
    def _single_defer(decision_groups: list[tuple[object, ...]]) -> DeferExecution | None:
        decisions = tuple(decision for group in decision_groups for decision in group)
        if not any(type(decision) is DeferExecution for decision in decisions):
            return None
        if len(decision_groups) != 1 or len(decision_groups[0]) != 1:
            raise ValueError("DeferExecution must be the only Decision for one Fact")
        decision = decision_groups[0][0]
        if type(decision) is not DeferExecution:
            raise ValueError("DeferExecution must not be mixed with another Decision")
        return decision

    async def _hold_deferred(
        self,
        db: object,
        evidence: InboundEvidence,
        prepared: tuple[_PreparedFact, ...],
        decision: DeferExecution,
        now: datetime,
    ) -> None:
        if len(prepared) != 1:
            raise ValueError("DeferExecution requires exactly one prepared Fact")
        item = prepared[0]
        if (
            decision.material_execution_id != item.execution.execution_code
            or decision.fact_id != item.fact.fact_id
            or item.fact.evidence_id != str(evidence.id)
        ):
            raise ValueError("DeferExecution identity does not match the claimed Fact")
        evidence.decision_digest = None
        evidence.decision_claim_token = None
        evidence.decision_claim_expires_at = None
        evidence.decision_next_attempt_at = now
        _ = await self._execution_service.transition(
            db,
            item.execution,
            target=MaterialExecutionStatus.HOLD,
            changed_at=now,
            reason_code=decision.reason_code,
            evidence_id=cast("int", evidence.id),
        )
        await self._evidences.flush(db)

    def _decision_groups(self, prepared: tuple[_PreparedFact, ...]) -> list[tuple[object, ...]]:
        decision_groups: list[tuple[object, ...]] = []
        for item in prepared:
            handler = self._plugins.resolve_handler(item.plugin_key, item.plugin_version, item.fact)
            group = handler(item.fact)
            if type(group) is not tuple or not group:
                raise ValueError("handler must return a non-empty Decision tuple")
            decision_groups.append(group)
        return decision_groups

    async def _claimed(self, db: object, evidence_id: int, token: str, now: datetime) -> InboundEvidence:
        evidence = await self._evidences.get_decision_claim_for_update(
            db,
            evidence_id=evidence_id,
            claim_token=token,
            now=now,
        )
        if evidence is None:
            raise RuntimeError("Decision claim is missing or expired")
        return evidence

    async def _load_workline(self, db: object, evidence: InboundEvidence) -> WorkLine:
        if evidence.workline_id is None:
            raise ValueError("evidence 缺少 workline_id")
        workline = await self._worklines.get_for_update(db, evidence.workline_id)
        if workline is None or not workline.is_active:
            raise ValueError("evidence 未关联活动 WorkLine")
        return workline

    async def _load_workline_for_execution(self, db: object, execution: MaterialExecution) -> WorkLine:
        workline = await self._worklines.get_for_update(db, execution.workline_id)
        if workline is None or not workline.is_active:
            raise ValueError("execution 未关联活动 WorkLine")
        return workline

    async def _restore_workline_from_execution(self, db: object, evidence: InboundEvidence) -> None:
        if evidence.workline_id is not None or evidence.material_execution_id is None:
            return
        execution = await self._load_execution(db, evidence)
        evidence.workline_id = execution.workline_id
        await self._evidences.flush(db)

    async def _load_execution(self, db: object, evidence: InboundEvidence) -> MaterialExecution:
        if evidence.material_execution_id is None:
            raise ValueError("evidence 缺少 material_execution_id")
        execution = await self._executions.get_by_id_for_update(db, evidence.material_execution_id)
        if execution is None:
            raise LookupError("MaterialExecution 不存在")
        return execution

    async def _augment_fact(self, db: object, workline: WorkLine, base_fact: FactReference) -> FactReference:
        factory = self._plugins.resolve_fact_factory(workline.plugin_key, workline.plugin_version)
        fact = await factory.build(db, base_fact)
        if not isinstance(fact, FactReference):
            raise TypeError("PluginFactFactory must return FactReference")
        if (
            fact.fact_id != base_fact.fact_id
            or fact.evidence_id != base_fact.evidence_id
            or fact.fact_version != base_fact.fact_version
            or fact.material_execution_id != base_fact.material_execution_id
        ):
            raise ValueError("PluginFactFactory must preserve base Fact identity")
        return fact

    async def _prepare_facts_in_session(
        self,
        db: object,
        evidence: InboundEvidence,
        now: datetime,
    ) -> tuple[_PreparedFact, ...]:
        await self._restore_workline_from_execution(db, evidence)
        workline = await self._load_workline(db, evidence)
        execution = await self._load_or_correlate_execution(db, evidence, workline, now)
        causal_evidence = None
        if (
            evidence.kind == InboundEvidenceKind.TRANSPORT_RESULT
            and MaterialExecutionStatus(execution.status) is MaterialExecutionStatus.RECONCILING
        ):
            causal_evidence = await self._evidences.get_by_id_for_update(db, execution.last_transition_evidence_id)
        position_bindings = (
            tuple(await self._worklines.list_position_bindings(db, workline.id))
            if evidence.kind == InboundEvidenceKind.WMS_EVENT
            else ()
        )
        fact = await self._augment_fact(
            db,
            workline,
            self._fact_builder.build(
                evidence,
                execution,
                causal_evidence=causal_evidence,
                position_bindings=position_bindings,
            ),
        )
        return (_PreparedFact(fact, workline.plugin_key, workline.plugin_version, execution),)

    async def _transition_all_executions(
        self,
        db: object,
        evidence: InboundEvidence,
        changed_at: datetime,
        *,
        reason_code: str,
    ) -> None:
        executions: list[MaterialExecution] = []
        if evidence.material_execution_id is not None:
            executions.append(await self._load_execution(db, evidence))
        for execution in executions:
            if MaterialExecutionStatus(execution.status) is MaterialExecutionStatus.CLOSED:
                continue
            _ = await self._execution_service.transition(
                db,
                execution,
                target=MaterialExecutionStatus.RECONCILING,
                changed_at=changed_at,
                reason_code=reason_code,
                evidence_id=cast("int", evidence.id),
            )

    async def _load_or_correlate_execution(
        self,
        db: object,
        evidence: InboundEvidence,
        workline: WorkLine,
        now: datetime,
    ) -> MaterialExecution:
        if evidence.material_execution_id is not None:
            return await self._load_execution(db, evidence)
        if evidence.kind != InboundEvidenceKind.DEVICE_EVENT:
            raise ValueError(f"{InboundEvidenceKind(evidence.kind).value} evidence 缺少 material_execution_id")
        correlator = self._plugins.resolve_initial_execution_correlator(workline.plugin_key, workline.plugin_version)
        descriptor = await correlator.correlate(db, str(evidence.id))
        if descriptor is None:
            raise ValueError("initial evidence cannot be correlated")
        execution = await self._execution_service.create_or_get_for_initial_evidence(
            db,
            execution_code=descriptor.execution_code,
            material_trace_id=descriptor.material_trace_id,
            workline_id=workline.id,
            changed_at=now,
            evidence_id=cast("int", evidence.id),
        )
        if execution.id is None:
            raise RuntimeError("初始 MaterialExecution 未持久化")
        evidence.material_execution_id = execution.id
        await self._evidences.flush(db)
        return execution


__all__ = ["FactProcessor"]
