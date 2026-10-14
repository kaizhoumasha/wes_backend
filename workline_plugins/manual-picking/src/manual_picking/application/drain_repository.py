"""按插件 execution 读取当前 READY；原可靠义务和 Transport 保留冻结身份。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased
from wes_plugin_sdk import ReturnBufferDrainReady

from src.app.execution.models import PositionProjection, TransportDecisionBinding
from src.app.execution.repositories.transport_decision_binding_repository import transport_decision_binding_repository
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainRecord, ReturnBufferDrainResultReader

from .drain_execution_model import ManualPickingDrainExecution
from .drain_execution_repository import DrainExecutionRepository

DRAIN_RACK_IN_STEP = "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN"
DRAIN_RACK_ROTATE_STEP = "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_ROTATE"
DRAIN_RACK_OUT_STEP = "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT"
RETIRED_DRAIN_RACK_OUT_STEP = "MANUAL_PICKING_RETIRED_DRAIN_RACK_OUT"
SOURCE_RACK_IN_STEP = "PICKING_TASK_BIN_SOURCE_RACK_IN"
SOURCE_RACK_ROTATE_STEP = "MANUAL_PICKING_SOURCE_RACK_ROTATE"
SOURCE_RACK_OUT_STEP = "MANUAL_PICKING_SOURCE_RACK_OUT"
RETURN_RACK_ROTATE_STEP = "MANUAL_PICKING_RETURN_RACK_ROTATE"
RETURN_RACK_OUT_STEP = "MANUAL_PICKING_RETURN_RACK_OUT"


def current_rack_action_predicate(binding: Any, task: Any, owner: Any) -> Any:
    """当前动作/物理事实/有效窗口才构成接管；闭合历史不占有货架。"""
    window = aliased(TransportDecisionBinding)
    same_owner = and_(
        binding.workline_id == owner.workline_id,
        and_(binding.picking_task_id.is_not(None), binding.picking_task_id == owner.picking_task_id)
        if owner.picking_task_id is not None
        else and_(binding.picking_task_id.is_(None), binding.source_evidence_id == owner.source_evidence_id),
    )
    active_owner = (
        select(window.id)
        .where(
            window.workline_id == binding.workline_id,
            window.resource_fence_id == binding.resource_fence_id,
            window.window_target_location_code.is_not(None),
            window.window_released_at.is_(None),
            or_(
                window.picking_task_id == binding.picking_task_id,
                and_(
                    window.picking_task_id.is_(None),
                    binding.picking_task_id.is_(None),
                    window.source_evidence_id == binding.source_evidence_id,
                ),
            ),
        )
        .exists()
    )
    current_fact = (
        select(PositionProjection.id)
        .where(
            PositionProjection.object_type == "RACK",
            PositionProjection.object_id == binding.resource_fence_id,
            PositionProjection.source_causal_token == binding.causal_token,
        )
        .exists()
    )
    return or_(
        task.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
        task.outcome_version > task.published_outcome_version,
        current_fact,
        and_(~same_owner, active_owner),
    )


class DrainRepository:
    def __init__(self, reader: Any = None, *, executions: Any = None) -> None:
        self._reader = reader or ReturnBufferDrainResultReader()
        self.executions = executions or DrainExecutionRepository()

    async def retired_clearance_racks(self, db: Any, *, workline_id: int) -> list[tuple[Any, Any, Any]]:
        """当前物理事实仍属于退役轮次时，原进场绑定承接独立清退，窗口无需重开。"""
        fact = aliased(TransportDecisionBinding)
        roots = (
            await db.execute(
                select(TransportDecisionBinding, PositionProjection)
                .join(
                    ManualPickingDrainExecution,
                    ManualPickingDrainExecution.decision_evidence_id == TransportDecisionBinding.source_evidence_id,
                )
                .join(PositionProjection, PositionProjection.object_id == TransportDecisionBinding.resource_fence_id)
                .join(fact, fact.causal_token == PositionProjection.source_causal_token)
                .where(
                    ManualPickingDrainExecution.workline_id == workline_id,
                    ManualPickingDrainExecution.status == "EXCEPTION_RETIRED",
                    TransportDecisionBinding.workline_id == workline_id,
                    TransportDecisionBinding.picking_task_id.is_(None),
                    TransportDecisionBinding.step == DRAIN_RACK_IN_STEP,
                    TransportDecisionBinding.window_target_location_code.is_not(None),
                    PositionProjection.workline_id == workline_id,
                    PositionProjection.object_type == "RACK",
                    PositionProjection.position_unknown.is_(False),
                    PositionProjection.source_effect_phase == "FINAL_RESULT",
                    PositionProjection.position_json["kind"].as_string() == "RACK_POSITION",
                    PositionProjection.position_json["location_code"].as_string()
                    == TransportDecisionBinding.window_target_location_code,
                    fact.workline_id == workline_id,
                    fact.picking_task_id.is_(None),
                    fact.resource_fence_id == TransportDecisionBinding.resource_fence_id,
                    fact.source_evidence_id == TransportDecisionBinding.source_evidence_id,
                    fact.causal_token >= TransportDecisionBinding.causal_token,
                )
                .order_by(TransportDecisionBinding.causal_token.desc())
            )
        ).all()
        contexts, seen = [], set()
        for root, position in roots:
            rack_id = root.resource_fence_id
            if rack_id in seen:
                continue
            seen.add(rack_id)
            await transport_decision_binding_repository.lock_object_authority(db, object_type="RACK", object_id=rack_id)
            await db.refresh(position)
            if (
                position.workline_id != workline_id
                or position.position_unknown
                or position.source_effect_phase != "FINAL_RESULT"
                or position.source_causal_token is None
                or position.source_causal_token < root.causal_token
                or position.position_json
                != {"kind": "RACK_POSITION", "location_code": root.window_target_location_code}
            ):
                continue
            latest = (
                await db.execute(
                    select(TransportDecisionBinding, TransportTask)
                    .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                    .where(
                        TransportDecisionBinding.resource_fence_id == rack_id,
                        TransportDecisionBinding.causal_token >= root.causal_token,
                        TransportTask.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                        or_(
                            and_(
                                TransportDecisionBinding.workline_id == root.workline_id,
                                TransportDecisionBinding.picking_task_id.is_(None),
                                TransportDecisionBinding.source_evidence_id == root.source_evidence_id,
                            ),
                            current_rack_action_predicate(TransportDecisionBinding, TransportTask, root),
                        ),
                    )
                    .order_by(TransportDecisionBinding.causal_token.desc())
                    .limit(1)
                )
            ).one_or_none()
            if latest is None:
                continue
            binding, task = latest
            if (
                binding.workline_id != workline_id
                or binding.picking_task_id is not None
                or binding.source_evidence_id != root.source_evidence_id
                or binding.step
                not in {DRAIN_RACK_IN_STEP, DRAIN_RACK_ROTATE_STEP, DRAIN_RACK_OUT_STEP, RETIRED_DRAIN_RACK_OUT_STEP}
            ):
                continue
            in_flight = await db.scalar(
                select(TransportTask.id)
                .where(
                    TransportTask.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                    TransportTask.request_json["rack_id"].as_string() == rack_id,
                    TransportTask.created_at >= root.created_at,
                    TransportTask.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                )
                .limit(1)
            )
            if in_flight is not None:
                continue
            contexts.append((root, task, position))
        return contexts

    async def current(self, db: Any, workline_id: int) -> ReturnBufferDrainRecord | None:
        execution = await self.executions.active_for_workline(db, workline_id)
        if execution is None:
            return await self._reader.pending_for_workline(db, workline_id=workline_id)
        decision = await self._reader.for_operation(
            db, workline_id=workline_id, operation_id=execution.drain_operation_id
        )
        if (
            decision is None
            or not isinstance(decision.result, ReturnBufferDrainReady)
            or decision.evidence_id != execution.decision_evidence_id
        ):
            raise ValueError("active drain execution READY Evidence missing or mismatched")
        return decision

    async def complete_if_departed(self, db: Any, decision: ReturnBufferDrainRecord, *, closed_at: Any) -> bool:
        for rack in decision.result.racks:
            departure = await self.transport(db, decision, DRAIN_RACK_OUT_STEP, rack.rack_id)
            if departure is None or not self._accepted_or_terminal(departure):
                return False
        execution = await self.executions.for_evidence(
            db, workline_id=decision.workline_id, evidence_id=decision.evidence_id
        )
        if execution is None:
            raise ValueError("drain READY execution missing")
        return await self.executions.close_in_session(db, execution, status="COMPLETED", closed_at=closed_at)

    async def for_evidence(self, db: Any, workline_id: int, evidence_id: int) -> ReturnBufferDrainRecord | None:
        """按冻结 evidence_id 独立定位历史 drain 决定，不依赖 current() 的"最新"指针（R5）。

        current drain 切换到新决定后，仍未完结的原退场重试要能按自己冻结的 evidence_id
        重新找到当时那条决定，而不是被当作义务已经消失。
        """
        execution = await self.executions.for_evidence(db, workline_id=workline_id, evidence_id=evidence_id)
        if execution is None:
            return None
        return await self._reader.for_operation(db, workline_id=workline_id, operation_id=execution.drain_operation_id)

    async def transport(
        self,
        db: Any,
        decision: ReturnBufferDrainRecord,
        step: str,
        rack_id: str,
        face: str | None = None,
    ) -> Any:
        rows = (
            await db.execute(
                select(TransportDecisionBinding, TransportTask)
                .outerjoin(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .where(
                    TransportDecisionBinding.workline_id == decision.workline_id,
                    TransportDecisionBinding.picking_task_id.is_(None),
                    TransportDecisionBinding.source_evidence_id == decision.evidence_id,
                    TransportDecisionBinding.resource_fence_id == rack_id,
                    TransportDecisionBinding.step == step,
                )
                .order_by(TransportDecisionBinding.id.desc())
            )
        ).all()
        for binding, task in rows:
            if task is not None and (face is None or task.request_json.get("target_face") == face):
                return self._validate_transport(decision, rack_id, binding.correlation_id, binding, task)
        return None

    @staticmethod
    def _validate_transport(
        decision: ReturnBufferDrainRecord, rack_id: str, correlation_id: str, binding: Any, task: Any
    ) -> Any:
        if (
            not isinstance(decision.result, ReturnBufferDrainReady)
            or task is None
            or binding.workline_id != decision.workline_id
            or binding.picking_task_id is not None
            or binding.correlation_id != correlation_id
            or binding.source_evidence_id != decision.evidence_id
            or binding.resource_fence_id != rack_id
            or task.authority_workline_id != decision.workline_id
            or task.kind != ("RACK_ROTATE" if binding.step == DRAIN_RACK_ROTATE_STEP else "RACK_MOVE")
            or task.request_json.get("rack_id") != rack_id
        ):
            raise ValueError("drain Transport identity/evidence/owner mismatch")
        return task

    async def arrival_matches(self, db: Any, transport: Any, projection: Any, rack_id: str, face: str) -> bool:
        if transport.transport_task_id != projection.source_transport_task_id or transport.status != "SUCCEEDED":
            return False
        members = (
            await db.scalars(
                select(TransportMember).where(TransportMember.transport_task_id == transport.transport_task_id)
            )
        ).all()
        return (
            len(members) == 1
            and members[0].object_type == "RACK"
            and members[0].object_id == rack_id
            and members[0].status == "SUCCEEDED"
            and not members[0].position_unknown
            and members[0].final_position_json == projection.position_json
            and members[0].arrival_face == face
        )

    async def has_retired_unclosed_inbound(
        self,
        db: Any,
        *,
        workline_id: int,
        rack_id: str,
        target_location_code: str,
        target_face: str,
    ) -> bool:
        tasks = (
            await db.scalars(
                select(TransportTask)
                .join(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .join(
                    ManualPickingDrainExecution,
                    ManualPickingDrainExecution.decision_evidence_id == TransportDecisionBinding.source_evidence_id,
                )
                .where(
                    ManualPickingDrainExecution.workline_id == workline_id,
                    ManualPickingDrainExecution.status == "EXCEPTION_RETIRED",
                    TransportDecisionBinding.workline_id == workline_id,
                    TransportDecisionBinding.picking_task_id.is_(None),
                    TransportDecisionBinding.step == DRAIN_RACK_IN_STEP,
                    TransportDecisionBinding.resource_fence_id == rack_id,
                    TransportTask.authority_workline_id == workline_id,
                    TransportTask.status.in_(("PENDING", "ACCEPTED", "RECONCILING")),
                )
            )
        ).all()
        # 原未知动作仍是运行依赖；新 drain 不能以换身份重复原进场。
        return any(
            task.request_json.get("rack_id") == rack_id
            and task.request_json.get("source") == {"kind": "RACK", "location_code": rack_id}
            and task.request_json.get("target") == {"kind": "RACK_POSITION", "location_code": target_location_code}
            and task.request_json.get("target_face") == target_face
            for task in tasks
        )

    async def has_unclosed_rack_action(self, db: Any, decision: ReturnBufferDrainRecord, rack_id: str) -> bool:
        bindings = TransportDecisionBinding.__table__.c
        transports = TransportTask.__table__.c
        return (
            await db.scalar(
                select(transports.id)
                .join(TransportDecisionBinding, bindings.client_request_id == transports.client_request_id)
                .where(
                    bindings.workline_id == decision.workline_id,
                    bindings.picking_task_id.is_(None),
                    bindings.source_evidence_id == decision.evidence_id,
                    bindings.resource_fence_id == rack_id,
                    bindings.step.in_((DRAIN_RACK_IN_STEP, DRAIN_RACK_ROTATE_STEP, DRAIN_RACK_OUT_STEP)),
                    or_(
                        transports.status.in_(("PENDING", "RECONCILING")),
                        transports.outcome_version > transports.published_outcome_version,
                    ),
                )
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _accepted_or_terminal(task: Any) -> bool:
        return (
            task.status in {"ACCEPTED", "SUCCEEDED"}
            or (task.status == "FAILED" and getattr(task, "reason_code", None) != "RCS_TASK_CANCELLED")
            or (task.status == "RECONCILING" and task.result_deadline_at is not None)
        )


__all__ = [
    "DRAIN_RACK_IN_STEP",
    "DRAIN_RACK_OUT_STEP",
    "DRAIN_RACK_ROTATE_STEP",
    "RETIRED_DRAIN_RACK_OUT_STEP",
    "RETURN_RACK_OUT_STEP",
    "RETURN_RACK_ROTATE_STEP",
    "SOURCE_RACK_IN_STEP",
    "SOURCE_RACK_OUT_STEP",
    "SOURCE_RACK_ROTATE_STEP",
    "DrainRepository",
]
