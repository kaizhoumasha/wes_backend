"""从 Confirmation 前驱链与原货架 Transport 推导排空状态，不新增生命周期存储。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from wes_plugin_sdk import ReturnBufferDrainReady, ReturnBufferDrainWait

from src.app.execution.models import TransportDecisionBinding
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainRecord, ReturnBufferDrainResultReader

DRAIN_RACK_IN_STEP = "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN"
DRAIN_RACK_OUT_STEP = "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT"
SOURCE_RACK_IN_STEP = "PICKING_TASK_BIN_SOURCE_RACK_IN"
SOURCE_RACK_ROTATE_STEP = "MANUAL_PICKING_SOURCE_RACK_ROTATE"
SOURCE_RACK_OUT_STEP = "MANUAL_PICKING_SOURCE_RACK_OUT"


class DrainRepository:
    def __init__(self, reader: Any = None) -> None:
        self._reader = reader or ReturnBufferDrainResultReader()

    async def current(self, db: Any, workline_id: int) -> ReturnBufferDrainRecord | None:
        # 插件只决定物理关闭依据。宿主以 checkpoint 为界，一次返回校验后的 typed 后缀。
        checkpoint = (
            await db.execute(
                select(TransportDecisionBinding, TransportTask)
                .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .where(
                    TransportDecisionBinding.workline_id == workline_id,
                    TransportDecisionBinding.step == DRAIN_RACK_OUT_STEP,
                    or_(
                        TransportTask.status.in_(("ACCEPTED", "SUCCEEDED", "FAILED")),
                        (TransportTask.status == "RECONCILING") & TransportTask.result_deadline_at.is_not(None),
                    ),
                )
                .order_by(TransportTask.created_at.desc(), TransportDecisionBinding.id.desc())
                .limit(1)
            )
        ).one_or_none()
        records = await self._reader.history(
            db,
            workline_id=workline_id,
            after_operation_id=checkpoint[0].correlation_id.removeprefix("drain:") if checkpoint is not None else None,
        )
        current, previous = None, None
        if checkpoint is not None:
            if not records:
                raise ValueError("drain history checkpoint missing")
            previous = records[0]
            self._validate_transport(previous, *checkpoint)
            records = records[1:]
        # 两项 tail 的首项是上一轮已经检查的链头；只重新验证最新直接前驱。
        # 仅有一项时必须是新 root，不能把缺失前驱当成截断历史。
        if len(records) == 2:
            first = records[0]
            if first.intent.drain_reason != "PICKING_TASK_COMPLETED":
                raise ValueError("unsupported manual-picking drain trigger")
            if previous is not None and first.intent.previous_operation_id == previous.intent.operation_id:
                raise ValueError("drain successor cannot continue closed READY checkpoint")
            current = previous = first
            records = records[1:]
        for record in records:
            intent = record.intent
            if intent.drain_reason != "PICKING_TASK_COMPLETED":
                raise ValueError("unsupported manual-picking drain trigger")
            if intent.previous_operation_id is None:
                if current is not None:
                    raise ValueError("two active drain chains for WorkLine")
            elif (
                previous is None
                or current is None
                or intent.previous_operation_id != previous.intent.operation_id
                or not isinstance(previous.result, ReturnBufferDrainWait)
                or previous.completed_at is None
                or record.created_at < previous.completed_at + timedelta(milliseconds=previous.result.retry_after_ms)
                or (intent.workline_code, intent.plugin_key, intent.drain_reason)
                != (previous.intent.workline_code, previous.intent.plugin_key, previous.intent.drain_reason)
            ):
                raise ValueError("broken, forked or premature drain predecessor")
            current = previous = record
        if current is not None and isinstance(current.result, ReturnBufferDrainReady):
            departure = await self.transport(db, current, DRAIN_RACK_OUT_STEP)
            if departure is not None and (
                departure.status in {"ACCEPTED", "SUCCEEDED", "FAILED"}
                or (departure.status == "RECONCILING" and departure.result_deadline_at is not None)
            ):
                return None
        return current

    async def is_reserved(self, db: Any, workline_id: int) -> bool:
        return await self.current(db, workline_id) is not None

    async def has_completed_task(self, db: Any, workline_id: int) -> bool:
        return (
            await db.scalar(
                select(PickingTask.id)
                .where(PickingTask.workline_id == workline_id, PickingTask.status == "EXECUTION_COMPLETED")
                .limit(1)
            )
            is not None
        )

    async def transport(self, db: Any, decision: ReturnBufferDrainRecord, step: str) -> Any:
        row = (
            await db.execute(
                select(TransportDecisionBinding, TransportTask)
                .outerjoin(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .where(
                    TransportDecisionBinding.workline_id == decision.workline_id,
                    TransportDecisionBinding.correlation_id == f"drain:{decision.intent.operation_id}",
                    TransportDecisionBinding.step == step,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return self._validate_transport(decision, *row)

    @staticmethod
    def _validate_transport(decision: ReturnBufferDrainRecord, binding: Any, task: Any) -> Any:
        if (
            not isinstance(decision.result, ReturnBufferDrainReady)
            or task is None
            or binding.workline_id != decision.workline_id
            or binding.correlation_id != f"drain:{decision.intent.operation_id}"
            or binding.source_evidence_id != decision.evidence_id
            or binding.resource_fence_id != decision.result.rack_id
            or task.authority_workline_id != decision.workline_id
            or task.kind != "RACK_MOVE"
            or task.request_json.get("rack_id") != decision.result.rack_id
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

    async def has_unclosed_rack_action(self, db: Any, workline_id: int) -> bool:
        return (
            await db.scalar(
                select(TransportTask.id)
                .join(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .where(
                    TransportDecisionBinding.workline_id == workline_id,
                    TransportDecisionBinding.step.in_(
                        (
                            SOURCE_RACK_IN_STEP,
                            SOURCE_RACK_ROTATE_STEP,
                            SOURCE_RACK_OUT_STEP,
                            DRAIN_RACK_IN_STEP,
                            DRAIN_RACK_OUT_STEP,
                        )
                    ),
                    TransportTask.authority_workline_id == workline_id,
                    TransportTask.kind.in_(("RACK_MOVE", "RACK_ROTATE")),
                    or_(
                        TransportTask.status.in_(("RECONCILING", "FAILED")),
                        TransportTask.outcome_version > TransportTask.published_outcome_version,
                    ),
                )
                .limit(1)
            )
            is not None
        )
