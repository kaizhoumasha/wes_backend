"""有界领取已验证 evidence，调用纯 handler 并原子应用封闭 Decision。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from wes_plugin_sdk import FactReference

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceExecutionBinding,
    InboundEvidenceKind,
    MaterialExecution,
)
from src.app.execution.models.material_execution import MaterialExecutionStatus
from src.app.execution.repositories import (
    inbound_evidence_execution_binding_repository,
    inbound_evidence_repository,
    material_execution_repository,
)
from src.app.execution.services.decision_applier import DecisionApplier, decision_digest
from src.app.execution.services.fact_builder import FactBuilder
from src.app.execution.services.material_execution_service import MaterialExecutionService
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.execution.plugin_binding import StaticPluginBinding

logger = logging.getLogger(__name__)

_CLAIM_SECONDS = 30
_MAX_ATTEMPTS = 5
_MAX_BATCH_SIZE = 100
_MAX_BACKOFF_SECONDS = 300


class EvidenceRepositoryPort(Protocol):
    async def claim_decision_batch(self, db: object, **kwargs: object) -> list[InboundEvidence]: ...

    async def get_decision_claim_for_update(self, db: object, **kwargs: object) -> InboundEvidence | None: ...

    async def flush(self, db: object) -> None: ...


class ExecutionRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: object, execution_id: int) -> MaterialExecution | None: ...

    async def get_by_execution_code_for_update(
        self,
        db: object,
        execution_code: str,
    ) -> MaterialExecution | None: ...


class EpochRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: object, line_run_epoch_id: int) -> LineRunEpoch | None: ...


class EvidenceExecutionBindingRepositoryPort(Protocol):
    async def list_for_evidence_for_update(
        self,
        db: object,
        evidence_id: int,
    ) -> list[InboundEvidenceExecutionBinding]: ...


class InitialExecutionServicePort(Protocol):
    async def create_or_get_for_initial_evidence(self, db: object, **kwargs: object) -> MaterialExecution: ...

    async def transition(self, db: object, execution: MaterialExecution, **kwargs: object) -> MaterialExecution: ...


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
        session_factory: object,
        plugin_binding: StaticPluginBinding,
        decision_applier: DecisionApplier,
        evidence_repository: EvidenceRepositoryPort | None = None,
        execution_repository: ExecutionRepositoryPort | None = None,
        epoch_repository: EpochRepositoryPort | None = None,
        evidence_execution_binding_repository: EvidenceExecutionBindingRepositoryPort | None = None,
        material_execution_service: InitialExecutionServicePort | None = None,
        fact_builder: FactBuilder | None = None,
        clock: object = timezone.now_for_db,
        token_factory: object = lambda: uuid.uuid4().hex,
    ) -> None:
        self._sessions = session_factory
        self._plugins = plugin_binding
        self._applier = decision_applier
        self._evidences = evidence_repository or inbound_evidence_repository
        self._executions = execution_repository or material_execution_repository
        self._epochs = epoch_repository or line_run_epoch_repository
        self._evidence_execution_bindings = (
            evidence_execution_binding_repository or inbound_evidence_execution_binding_repository
        )
        self._execution_service = material_execution_service or MaterialExecutionService()
        self._fact_builder = fact_builder or FactBuilder()
        self._clock = cast("callable", clock)
        self._token_factory = cast("callable", token_factory)

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
                decision_groups = []
                for item in prepared:
                    handler = self._plugins.resolve_handler(item.plugin_key, item.plugin_version, item.fact)
                    group = handler(item.fact)
                    if type(group) is not tuple or not group:
                        raise ValueError("handler must return a non-empty Decision tuple")
                    decision_groups.append(group)
                decisions = tuple(decision for group in decision_groups for decision in group)
                digest = decision_digest(decisions)
                if not await self._record_digest(evidence_id, token, digest):
                    processed += 1
                    continue
                await self._apply(evidence_id, token, digest, tuple(decision_groups))
                processed += 1
            except Exception:  # worker 必须隔离单条 evidence，并通过持久状态有界恢复。
                logger.exception("execution.fact_processing_failed", extra={"evidence_id": evidence_id})
                try:
                    await self._record_failure(evidence_id, token)
                except Exception:
                    logger.exception("execution.fact_failure_recording_failed", extra={"evidence_id": evidence_id})
        return processed

    async def _prepare_fact(self, evidence_id: int, token: str) -> tuple[_PreparedFact, ...]:
        now = self._clock()
        async with self._sessions.begin() as db:
            evidence = await self._claimed(db, evidence_id, token, now)
            return await self._prepare_facts_in_session(db, evidence, now)

    async def _record_digest(self, evidence_id: int, token: str, digest: str) -> bool:
        now = self._clock()
        async with self._sessions.begin() as db:
            evidence = await self._claimed(db, evidence_id, token, now)
            if evidence.decision_digest is None:
                evidence.decision_digest = digest
                await self._evidences.flush(db)
                return True
            if evidence.decision_digest == digest:
                return True
            evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
            evidence.decision_claim_token = None
            evidence.decision_claim_expires_at = None
            evidence.decision_next_attempt_at = None
            await self._transition_all_executions(db, evidence, now, reason_code="DECISION_DIGEST_CONFLICT")
            await self._evidences.flush(db)
            return False

    async def _apply(
        self,
        evidence_id: int,
        token: str,
        digest: str,
        decision_groups: tuple[tuple[object, ...], ...],
    ) -> None:
        now = self._clock()
        async with self._sessions.begin() as db:
            evidence = await self._claimed(db, evidence_id, token, now)
            if evidence.decision_digest != digest:
                raise RuntimeError("fenced Decision digest changed")
            prepared = await self._prepare_facts_in_session(db, evidence, now)
            if len(prepared) != len(decision_groups):
                raise RuntimeError("frozen reconciliation Fact membership changed")
            flattened: list[object] = []
            for item, group in zip(prepared, decision_groups, strict=True):
                self._plugins.resolve_handler(item.plugin_key, item.plugin_version, item.fact)
                await self._applier.apply(db, evidence, item.execution, item.fact, group)
                flattened.extend(group)
            if decision_digest(tuple(flattened)) != digest:
                raise RuntimeError("Decision digest changed during application")
            evidence.published_at = now
            evidence.decision_claim_token = None
            evidence.decision_claim_expires_at = None
            evidence.decision_next_attempt_at = None
            await self._evidences.flush(db)

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
            if evidence.decision_attempt_count >= _MAX_ATTEMPTS:
                evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
                evidence.decision_next_attempt_at = None
                await self._transition_all_executions(db, evidence, now, reason_code="DECISION_APPLICATION_EXHAUSTED")
            else:
                backoff_seconds = min(2 ** max(evidence.decision_attempt_count - 1, 0), _MAX_BACKOFF_SECONDS)
                evidence.decision_next_attempt_at = now + timedelta(seconds=backoff_seconds)
            await self._evidences.flush(db)

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

    async def _load_epoch(self, db: object, evidence: InboundEvidence) -> LineRunEpoch:
        if evidence.line_run_epoch_id is None:
            raise ValueError("evidence 缺少 line_run_epoch_id")
        epoch = await self._epochs.get_by_id_for_update(db, evidence.line_run_epoch_id)
        if epoch is None or epoch.status != LineRunEpochStatus.ACTIVE:
            raise ValueError("evidence 未关联活动 LineRunEpoch")
        return epoch

    async def _load_epoch_for_execution(self, db: object, execution: MaterialExecution) -> LineRunEpoch:
        epoch = await self._epochs.get_by_id_for_update(db, execution.line_run_epoch_id)
        if epoch is None or epoch.status != LineRunEpochStatus.ACTIVE:
            raise ValueError("execution 未关联活动 LineRunEpoch")
        return epoch

    async def _restore_epoch_from_execution(self, db: object, evidence: InboundEvidence) -> None:
        if evidence.line_run_epoch_id is not None or evidence.material_execution_id is None:
            return
        execution = await self._load_execution(db, evidence)
        evidence.line_run_epoch_id = execution.line_run_epoch_id
        await self._evidences.flush(db)

    async def _load_execution(self, db: object, evidence: InboundEvidence) -> MaterialExecution:
        if evidence.material_execution_id is None:
            raise ValueError("evidence 缺少 material_execution_id")
        execution = await self._executions.get_by_id_for_update(db, evidence.material_execution_id)
        if execution is None:
            raise LookupError("MaterialExecution 不存在")
        return execution

    async def _augment_fact(self, epoch: LineRunEpoch, base_fact: FactReference) -> FactReference:
        factory = self._plugins.resolve_fact_factory(epoch.plugin_key, epoch.plugin_version)
        fact = await factory.build(base_fact)
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
        if evidence.kind == InboundEvidenceKind.WMS_EVENT:
            if evidence.id is None:
                raise ValueError("reconciliation evidence 未持久化")
            bindings = await self._evidence_execution_bindings.list_for_evidence_for_update(db, evidence.id)
            if not bindings:
                raise ValueError("reconciliation evidence 缺少冻结 execution bindings")
            prepared: list[_PreparedFact] = []
            for binding in bindings:
                execution = await self._executions.get_by_id_for_update(db, binding.material_execution_id)
                if execution is None:
                    raise LookupError("reconciliation MaterialExecution 不存在")
                epoch = await self._load_epoch_for_execution(db, execution)
                fact = await self._augment_fact(epoch, self._fact_builder.build_reconciliation(evidence, execution))
                prepared.append(_PreparedFact(fact, epoch.plugin_key, epoch.plugin_version, execution))
            return tuple(prepared)

        await self._restore_epoch_from_execution(db, evidence)
        epoch = await self._load_epoch(db, evidence)
        execution = await self._load_or_correlate_execution(db, evidence, epoch, now)
        fact = await self._augment_fact(epoch, self._fact_builder.build(evidence, execution))
        return (_PreparedFact(fact, epoch.plugin_key, epoch.plugin_version, execution),)

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
        elif evidence.kind == InboundEvidenceKind.WMS_EVENT and evidence.id is not None:
            bindings = await self._evidence_execution_bindings.list_for_evidence_for_update(db, evidence.id)
            for binding in bindings:
                execution = await self._executions.get_by_id_for_update(db, binding.material_execution_id)
                if execution is not None:
                    executions.append(execution)
        for execution in executions:
            if MaterialExecutionStatus(execution.status) is MaterialExecutionStatus.CLOSED:
                continue
            await self._execution_service.transition(
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
        epoch: LineRunEpoch,
        now: datetime,
    ) -> MaterialExecution:
        if evidence.material_execution_id is not None:
            return await self._load_execution(db, evidence)
        if evidence.kind != InboundEvidenceKind.DEVICE_EVENT:
            raise ValueError(f"{InboundEvidenceKind(evidence.kind).value} evidence 缺少 material_execution_id")
        correlator = self._plugins.resolve_initial_execution_correlator(epoch.plugin_key, epoch.plugin_version)
        descriptor = await correlator.correlate(str(evidence.id))
        if descriptor is None:
            raise ValueError("initial evidence cannot be correlated")
        execution = await self._execution_service.create_or_get_for_initial_evidence(
            db,
            execution_code=descriptor.execution_code,
            material_trace_id=descriptor.material_trace_id,
            workline_id=epoch.workline_id,
            line_run_epoch_id=cast("int", epoch.id),
            changed_at=now,
            evidence_id=cast("int", evidence.id),
        )
        if execution.id is None:
            raise RuntimeError("初始 MaterialExecution 未持久化")
        evidence.material_execution_id = execution.id
        await self._evidences.flush(db)
        return execution


__all__ = ["FactProcessor"]
