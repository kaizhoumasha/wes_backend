"""E03/E07 completed-or-reconciled 投格同步屏障。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold, RuntimeHoldStatus, RuntimeHoldType
from src.app.runtime.orchestration.repositories.runtime_hold_repository import (
    RuntimeHoldRepository,
    runtime_hold_repository,
)
from src.app.runtime.orchestration.repositories.wms_putaway_sync_barrier_repository import (
    WmsPutawaySyncBarrierRepository,
    WmsPutawaySyncObligation,
    wms_putaway_sync_barrier_repository,
)
from src.app.runtime.orchestration.wms_sync_obligation import (
    E03_CONFIRM_INBOUND,
    E07_NOTIFY_PKG_BINDING,
    WmsSyncObligationResolution,
)

WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES = (
    E03_CONFIRM_INBOUND,
    E07_NOTIFY_PKG_BINDING,
)


@dataclass(frozen=True, slots=True)
class WmsPutawaySyncBarrierGroup:
    """同一内部投格事实的稳定同步分组。"""

    execution_work_item_id: int
    correlation_id: str
    fact_version: str

    def __post_init__(self) -> None:
        correlation_id = self.correlation_id.strip()
        fact_version = self.fact_version.strip()
        if self.execution_work_item_id < 1:
            raise ValueError("execution_work_item_id must be positive")
        if not correlation_id:
            raise ValueError("correlation_id must be non-empty")
        if not fact_version:
            raise ValueError("fact_version must be non-empty")
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "fact_version", fact_version)

    @property
    def required_operation_identities(self) -> tuple[str, str]:
        return WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES


@dataclass(frozen=True, slots=True)
class WmsPutawaySyncBarrierEvaluation:
    """双义务屏障评估及对象 Hold 解除结果。"""

    satisfied: bool
    released: bool
    hold_id: int | None


class WmsPutawaySyncBarrierService:
    """创建并评估对象级 E03/E07 同步 Hold。"""

    def __init__(
        self,
        *,
        repository: WmsPutawaySyncBarrierRepository = wms_putaway_sync_barrier_repository,
        runtime_hold_repository: RuntimeHoldRepository = runtime_hold_repository,
    ) -> None:
        self._repository = repository
        self._runtime_hold_repository = runtime_hold_repository

    async def create_hold(
        self,
        db: Any,
        *,
        group: WmsPutawaySyncBarrierGroup,
        workline_id: int,
        session_id: int | None,
        trace_id: str | None,
    ) -> RuntimeHold:
        """幂等创建只阻断当前投格对象下游资格的同步 Hold。"""

        return await self._runtime_hold_repository.create_open_hold(
            db,
            hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
            workline_id=workline_id,
            session_id=session_id,
            trace_id=trace_id,
            source_kind="WMS_SYNC_OBLIGATION",
            source_reason="WMS_PUTAWAY_SYNC_PENDING",
            source_idempotency_key=self._source_idempotency_key(group),
            blocking=False,
            evidence_snapshot_json=self._group_evidence(group),
        )

    async def lock_group_for_dispatch(
        self,
        db: Any,
        *,
        dispatch_key: str,
    ) -> WmsPutawaySyncBarrierGroup | None:
        """由权威 Intent 派生分组，并以 WorkItem mutex 固定双义务锁序。"""

        identity = await self._repository.get_dispatch_identity(db, dispatch_key=dispatch_key)
        if identity is None:
            raise RuntimeError(f"WMS sync barrier dispatch intent does not exist: {dispatch_key}")
        if identity.operation_identity not in WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES:
            return None
        try:
            group = WmsPutawaySyncBarrierGroup(
                execution_work_item_id=int(identity.execution_work_item_id or 0),
                correlation_id=identity.correlation_id or "",
                fact_version=identity.fact_version or "",
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"WMS sync barrier dispatch identity is incomplete: {dispatch_key}") from exc
        if not await self._repository.lock_group_mutex(
            db,
            execution_work_item_id=group.execution_work_item_id,
            correlation_id=group.correlation_id,
        ):
            raise RuntimeError(f"WMS sync barrier work item does not exist: {dispatch_key}")
        # WorkItem 是组 mutex；持有后再按 operation_identity 固定顺序锁 E03/E07。
        await self._repository.load_group_for_update(db, group)
        return group

    async def evaluate_dispatch(
        self,
        db: Any,
        *,
        dispatch_key: str,
        locked_group: WmsPutawaySyncBarrierGroup | None = None,
    ) -> WmsPutawaySyncBarrierEvaluation | None:
        """按 dispatch 评估双义务；非 E03/E07 Intent 保持 no-op。"""

        group = locked_group or await self.lock_group_for_dispatch(db, dispatch_key=dispatch_key)
        if group is None:
            return None
        return await self.evaluate_and_release(db, group=group)

    async def evaluate_and_release(
        self,
        db: Any,
        *,
        group: WmsPutawaySyncBarrierGroup,
    ) -> WmsPutawaySyncBarrierEvaluation:
        """锁定同组两项义务；全部满足时恰好一次解除对象同步 Hold。"""

        hold = await self._repository.get_hold_for_update(
            db,
            source_idempotency_key=self._source_idempotency_key(group),
        )
        status = self._validate_object_eligibility_hold(hold, group=group)
        snapshot = await self._repository.load_group_for_update(db, group)
        satisfied = self._all_obligations_satisfied(snapshot.obligations, group=group)
        if status is RuntimeHoldStatus.RESOLVED:
            if not satisfied:
                raise RuntimeError("WMS sync barrier hold resolved before all obligations were satisfied")
            return WmsPutawaySyncBarrierEvaluation(
                satisfied=True,
                released=False,
                hold_id=hold.id,
            )
        if not satisfied:
            return WmsPutawaySyncBarrierEvaluation(satisfied=False, released=False, hold_id=hold.id)
        released = await self._repository.mark_hold_resolved(
            db,
            hold,
            release_evidence={
                **self._group_evidence(group),
                "resolution": "OBLIGATIONS_SATISFIED",
            },
        )
        return WmsPutawaySyncBarrierEvaluation(
            satisfied=True,
            released=released,
            hold_id=getattr(hold, "id", None),
        )

    @classmethod
    def _all_obligations_satisfied(
        cls,
        obligations: tuple[WmsPutawaySyncObligation, ...],
        *,
        group: WmsPutawaySyncBarrierGroup,
    ) -> bool:
        by_identity = {obligation.operation_identity: obligation for obligation in obligations}
        if set(by_identity) != set(WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES) or len(obligations) != 2:
            return False
        return all(
            cls._obligation_satisfied(by_identity[operation_identity], group=group)
            for operation_identity in WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES
        )

    @staticmethod
    def _obligation_satisfied(
        obligation: WmsPutawaySyncObligation,
        *,
        group: WmsPutawaySyncBarrierGroup,
    ) -> bool:
        if obligation.fact_version != group.fact_version or obligation.has_open_case:
            return False
        if obligation.intent_status == "COMPLETED":
            return True
        return any(
            WmsPutawaySyncBarrierService._matches_resolution(
                raw_decision,
                operation_identity=obligation.operation_identity,
                fact_version=group.fact_version,
            )
            for raw_decision in obligation.resolved_decisions
        )

    @staticmethod
    def _matches_resolution(
        raw_decision: dict[str, object],
        *,
        operation_identity: str,
        fact_version: str,
    ) -> bool:
        try:
            decision = WmsSyncObligationResolution.model_validate(raw_decision)
        except ValidationError:
            return False
        return (
            decision.resolved_operation_identity == operation_identity
            and decision.resolved_fact_version == fact_version
            and decision.resolution == "OBLIGATION_SATISFIED"
        )

    @classmethod
    def _validate_object_eligibility_hold(
        cls,
        hold: Any,
        *,
        group: WmsPutawaySyncBarrierGroup,
    ) -> RuntimeHoldStatus:
        if hold is None:
            raise RuntimeError("WMS sync barrier hold is missing")
        try:
            status = RuntimeHoldStatus(getattr(hold, "status", None))
        except ValueError as exc:
            raise RuntimeError("WMS sync barrier hold has an invalid status") from exc
        if (
            getattr(hold, "hold_type", None) != RuntimeHoldType.RUNTIME_RECONCILIATION
            or getattr(hold, "source_kind", None) != "WMS_SYNC_OBLIGATION"
            or getattr(hold, "source_reason", None) != "WMS_PUTAWAY_SYNC_PENDING"
            or getattr(hold, "blocking", None) is not False
            or getattr(hold, "evidence_snapshot_json", None) != cls._group_evidence(group)
        ):
            raise RuntimeError("WMS sync barrier hold is not the matching object eligibility hold")
        if status not in {RuntimeHoldStatus.OPEN, RuntimeHoldStatus.RESOLVED}:
            raise RuntimeError(f"WMS sync barrier hold has invalid lifecycle status: {status.value}")
        return status

    @staticmethod
    def _group_evidence(group: WmsPutawaySyncBarrierGroup) -> dict[str, object]:
        return {
            "barrier_kind": "WMS_PUTAWAY_E03_E07",
            "hold_scope": "OBJECT_ELIGIBILITY",
            "blocking": False,
            "execution_work_item_id": group.execution_work_item_id,
            "correlation_id": group.correlation_id,
            "fact_version": group.fact_version,
            "required_operation_identities": list(WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES),
        }

    @classmethod
    def _source_idempotency_key(cls, group: WmsPutawaySyncBarrierGroup) -> str:
        encoded = json.dumps(cls._group_evidence(group), sort_keys=True, separators=(",", ":")).encode()
        return f"wms-sync-obligation:{hashlib.sha256(encoded).hexdigest()}"


wms_putaway_sync_barrier_service = WmsPutawaySyncBarrierService()

__all__ = [
    "E03_CONFIRM_INBOUND",
    "E07_NOTIFY_PKG_BINDING",
    "WMS_PUTAWAY_SYNC_OPERATION_IDENTITIES",
    "WmsPutawaySyncBarrierEvaluation",
    "WmsPutawaySyncBarrierGroup",
    "WmsPutawaySyncBarrierService",
    "wms_putaway_sync_barrier_service",
]
