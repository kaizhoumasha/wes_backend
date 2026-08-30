"""WorkLine Repository 层"""

from typing import Any, cast

from sqlalchemy import String, and_, case, exists, func, literal, or_, select, true, union_all
from sqlalchemy import cast as sa_cast
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.execution.models.bin_execution import BinExecution, BinExecutionStatus
from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
)
from src.app.execution.models.material_execution import MaterialExecution, MaterialExecutionStatus
from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.app.resource.models.resource import BinPlacement, BinPlacementStatus, RackPlacement, RackPlacementStatus
from src.app.transport.contracts import TransportTaskStatus
from src.app.transport.models import TransportTask
from src.app.workline.models import WorkLine
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
from src.app.workline.models.safety import WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.database.base_repository import BaseRepository


class WorkLineRepository(BaseRepository[WorkLine]):
    """作业线数据访问层"""

    def __init__(self) -> None:
        super().__init__(WorkLine)

    async def get_by_line_code(
        self,
        db: AsyncSession,
        line_code: str,
    ) -> WorkLine | None:
        """根据作业线编码查询"""
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(
            select(WorkLine).where(
                columns.line_code == line_code,
                columns.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_for_update(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        populate_existing: bool = False,
    ) -> WorkLine | None:
        """根据 ID 查询并锁定 WorkLine，用于安全状态切换。"""

        columns = cast("Any", WorkLine).__table__.c
        statement = (
            select(WorkLine)
            .where(
                columns.id == workline_id,
                columns.is_deleted.is_(False),
            )
            .with_for_update()
        )
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_unfinished_workload_summary(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> dict[str, Any]:
        """以单条 SQL 返回七类 execution owner 的精确阻塞布尔值与诊断样本。"""

        epoch = cast("Any", LineRunEpoch).__table__.c
        material = cast("Any", MaterialExecution).__table__.c
        bin_execution = cast("Any", BinExecution).__table__.c
        command = cast("Any", DeviceCommand).__table__.c
        transport = cast("Any", TransportTask).__table__.c
        evidence = cast("Any", InboundEvidence).__table__.c
        confirmation = cast("Any", WmsConfirmation).__table__.c

        predicates = {
            "line_run_epochs": and_(
                epoch.workline_id == workline_id,
                epoch.status == LineRunEpochStatus.ACTIVE,
            ),
            "material_executions": and_(
                material.workline_id == workline_id,
                material.status != MaterialExecutionStatus.CLOSED,
            ),
            "bin_executions": and_(
                bin_execution.workline_id == workline_id,
                bin_execution.status == BinExecutionStatus.ACTIVE,
            ),
            "transport_tasks": and_(
                transport.authority_workline_id == workline_id,
                transport.status.in_(
                    (
                        TransportTaskStatus.PENDING,
                        TransportTaskStatus.ACCEPTED,
                        TransportTaskStatus.RECONCILING,
                    )
                ),
            ),
        }
        unclosed_command = and_(
            epoch.workline_id == workline_id,
            command.line_run_epoch_id == epoch.id,
            command.status.in_(
                (
                    CommandStatus.PENDING,
                    CommandStatus.DISPATCHING,
                    CommandStatus.ACKNOWLEDGED,
                    CommandStatus.RECONCILING,
                )
            ),
        )
        blocking_evidence = and_(
            epoch.workline_id == workline_id,
            evidence.line_run_epoch_id == epoch.id,
            or_(
                evidence.apply_status == InboundEvidenceApplyStatus.PENDING,
                evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING,
                and_(
                    evidence.apply_status == InboundEvidenceApplyStatus.APPLIED,
                    evidence.published_at.is_(None),
                    ~and_(
                        evidence.kind == InboundEvidenceKind.DEVICE_RESULT,
                        evidence.material_execution_id.is_(None),
                    ),
                ),
            ),
        )
        unfinished_confirmation = and_(
            material.workline_id == workline_id,
            confirmation.material_execution_id == material.id,
            confirmation.status != WmsConfirmationStatus.COMPLETED,
        )

        owner_queries = (
            ("line_run_epochs", select(epoch.id).where(predicates["line_run_epochs"])),
            ("material_executions", select(material.id).where(predicates["material_executions"])),
            ("bin_executions", select(bin_execution.id).where(predicates["bin_executions"])),
            ("device_commands", select(command.id).select_from(DeviceCommand, LineRunEpoch).where(unclosed_command)),
            ("transport_tasks", select(transport.id).where(predicates["transport_tasks"])),
            (
                "inbound_evidences",
                select(evidence.id).select_from(InboundEvidence, LineRunEpoch).where(blocking_evidence),
            ),
            (
                "wms_confirmations",
                select(confirmation.id).select_from(WmsConfirmation, MaterialExecution).where(unfinished_confirmation),
            ),
        )

        sample_union = union_all(
            self._sample_query(
                1, "line_run_epoch", epoch.id, epoch.status, epoch.epoch_code, predicates["line_run_epochs"]
            ),
            self._sample_query(
                2,
                "material_execution",
                material.id,
                material.status,
                material.execution_code,
                predicates["material_executions"],
            ),
            self._sample_query(
                3,
                "bin_execution",
                bin_execution.id,
                bin_execution.status,
                bin_execution.execution_code,
                predicates["bin_executions"],
            ),
            self._sample_query(
                4,
                "device_command",
                command.id,
                command.status,
                command.command_code,
                unclosed_command,
                from_models=(DeviceCommand, LineRunEpoch),
            ),
            self._sample_query(
                5,
                "transport_task",
                transport.id,
                transport.status,
                transport.transport_task_id,
                predicates["transport_tasks"],
            ),
            self._sample_query(
                6,
                "inbound_evidence",
                evidence.id,
                evidence.apply_status,
                evidence.source_identity,
                blocking_evidence,
                from_models=(InboundEvidence, LineRunEpoch),
            ),
            self._sample_query(
                7,
                "wms_confirmation",
                confirmation.id,
                confirmation.status,
                confirmation.operation_id,
                unfinished_confirmation,
                from_models=(WmsConfirmation, MaterialExecution),
            ),
        ).subquery("unfinished_owner_candidates")
        sample = (
            select(
                sample_union.c.owner_type,
                sample_union.c.owner_id,
                sample_union.c.status,
                sample_union.c.identity,
            )
            .order_by(sample_union.c.owner_order, sample_union.c.owner_id)
            .limit(1)
            .subquery("unfinished_owner_sample")
        )
        anchor = select(literal(1).label("value")).subquery("unfinished_owner_anchor")
        statement = select(
            *(exists(query.limit(1)).label(name) for name, query in owner_queries),
            sample.c.owner_type,
            sample.c.owner_id,
            sample.c.status,
            sample.c.identity,
        ).select_from(anchor.outerjoin(sample, true()))
        row = (await db.execute(statement)).one()
        by_type = {name: bool(getattr(row, name)) for name, _query in owner_queries}
        diagnostic_sample = None
        if row.owner_type is not None:
            diagnostic_sample = {
                "type": row.owner_type,
                "id": row.owner_id,
                "status": row.status,
                "identity": row.identity,
            }
        return {
            "count": sum(by_type.values()),
            "sample": diagnostic_sample,
            "by_type": by_type,
        }

    async def list_target_active_object_facts(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """聚合 target owners，供 WorkLine active-object API 只读投影。"""

        epoch = cast("Any", LineRunEpoch).__table__.c
        material = cast("Any", MaterialExecution).__table__.c
        bin_execution = cast("Any", BinExecution).__table__.c
        command = cast("Any", DeviceCommand).__table__.c
        transport = cast("Any", TransportTask).__table__.c
        confirmation = cast("Any", WmsConfirmation).__table__.c
        incident = cast("Any", WorklineSafetyIncident).__table__.c
        bin_placement = cast("Any", BinPlacement).__table__.c
        rack_placement = cast("Any", RackPlacement).__table__.c

        target_rows = union_all(
            self._active_object_query(
                "LINE_RUN_EPOCH",
                epoch.epoch_code,
                "LINE_RUN_EPOCH",
                epoch.status,
                literal("line_run_epoch:") + sa_cast(epoch.id, String),
                epoch.workline_id == workline_id,
                epoch.status == LineRunEpochStatus.ACTIVE,
            ),
            self._active_object_query(
                "MATERIAL_EXECUTION",
                material.material_trace_id,
                "MATERIAL_EXECUTION",
                material.execution_code,
                literal("material_execution:") + sa_cast(material.id, String),
                material.workline_id == workline_id,
                material.status != MaterialExecutionStatus.CLOSED,
            ),
            self._active_object_query(
                "BIN_EXECUTION",
                bin_execution.bin_id,
                "BIN_EXECUTION",
                bin_execution.execution_code,
                literal("bin_execution:") + sa_cast(bin_execution.id, String),
                bin_execution.workline_id == workline_id,
                bin_execution.status == BinExecutionStatus.ACTIVE,
            ),
            self._active_object_query(
                "DEVICE_COMMAND",
                command.command_code,
                "DEVICE_COMMAND",
                command.device_code,
                literal("device_command:") + sa_cast(command.id, String),
                epoch.workline_id == workline_id,
                command.line_run_epoch_id == epoch.id,
                command.status.in_(
                    (
                        CommandStatus.PENDING,
                        CommandStatus.DISPATCHING,
                        CommandStatus.ACKNOWLEDGED,
                        CommandStatus.RECONCILING,
                    )
                ),
                from_models=(DeviceCommand, LineRunEpoch),
            ),
            self._active_object_query(
                "TRANSPORT_TASK",
                transport.transport_task_id,
                "TRANSPORT_TASK",
                transport.status,
                literal("transport_task:") + sa_cast(transport.id, String),
                transport.authority_workline_id == workline_id,
                transport.status.in_(
                    (
                        TransportTaskStatus.PENDING,
                        TransportTaskStatus.ACCEPTED,
                        TransportTaskStatus.RECONCILING,
                    )
                ),
            ),
            self._active_object_query(
                "WMS_CONFIRMATION",
                confirmation.operation_id,
                "WMS_CONFIRMATION",
                confirmation.operation,
                literal("wms_confirmation:") + sa_cast(confirmation.id, String),
                material.workline_id == workline_id,
                confirmation.material_execution_id == material.id,
                confirmation.status != WmsConfirmationStatus.COMPLETED,
                from_models=(WmsConfirmation, MaterialExecution),
            ),
            self._active_object_query(
                "SAFETY_INCIDENT",
                sa_cast(incident.id, String),
                "SAFETY_INCIDENT",
                incident.event_type,
                literal("safety_incident:") + sa_cast(incident.id, String),
                incident.workline_id == workline_id,
                incident.status == WorklineSafetyIncidentStatus.ACTIVE,
            ),
            self._active_object_query(
                "BIN_RESOURCE",
                func.coalesce(bin_placement.bin_code, bin_placement.placeholder_key),
                "BIN_PLACEMENT",
                bin_placement.position_code,
                literal("resource_bin_placement:") + sa_cast(bin_placement.id, String),
                bin_placement.workline_id == workline_id,
                bin_placement.ended_at.is_(None),
                location_scope=bin_placement.position_type,
                location_code=bin_placement.position_code,
                location_conflict=case(
                    (bin_placement.placement_status == BinPlacementStatus.UNKNOWN, True),
                    else_=False,
                ),
            ),
            self._active_object_query(
                "RACK_RESOURCE",
                rack_placement.rack_code,
                "RACK_PLACEMENT",
                func.coalesce(rack_placement.position_code, rack_placement.location_code),
                literal("resource_rack_placement:") + sa_cast(rack_placement.id, String),
                rack_placement.workline_id == workline_id,
                rack_placement.ended_at.is_(None),
                location_scope=literal("WORKLINE_POSITION"),
                location_code=func.coalesce(rack_placement.position_code, rack_placement.location_code),
                location_conflict=case(
                    (rack_placement.placement_status == RackPlacementStatus.UNKNOWN, True),
                    else_=False,
                ),
            ),
        ).subquery("target_active_object_facts")
        result = await db.execute(
            select(target_rows)
            .where(target_rows.c.object_key.is_not(None), target_rows.c.object_key != "")
            .order_by(target_rows.c.object_type, target_rows.c.object_key, target_rows.c.owner_kind)
            .limit(limit)
        )
        return [dict(row._mapping) for row in result]

    @staticmethod
    def _active_object_query(
        object_type: str,
        object_key: Any,
        owner_kind: str,
        owner_code: Any,
        evidence_ref: Any,
        *predicates: Any,
        from_models: tuple[type[Any], ...] = (),
        location_scope: Any = None,
        location_code: Any = None,
        location_conflict: Any = False,
        presence_type: Any = None,
        transient_until: Any = None,
    ) -> Any:
        query = select(
            literal(object_type).label("object_type"),
            sa_cast(object_key, String).label("object_key"),
            literal(owner_kind).label("owner_kind"),
            sa_cast(owner_code, String).label("owner_code"),
            sa_cast(evidence_ref, String).label("evidence_ref"),
            sa_cast(location_scope, String).label("location_scope"),
            sa_cast(location_code, String).label("location_code"),
            location_conflict.label("location_conflict")
            if hasattr(location_conflict, "label")
            else literal(bool(location_conflict)).label("location_conflict"),
            sa_cast(presence_type, String).label("presence_type"),
            transient_until.label("transient_until")
            if hasattr(transient_until, "label")
            else literal(transient_until).label("transient_until"),
        )
        if from_models:
            query = query.select_from(*from_models)
        return query.where(*predicates)

    @staticmethod
    def _sample_query(
        owner_order: int,
        owner_type: str,
        owner_id: Any,
        status: Any,
        identity: Any,
        predicate: Any,
        *,
        from_models: tuple[type[Any], ...] = (),
    ) -> Any:
        query = select(
            literal(owner_order).label("owner_order"),
            literal(owner_type).label("owner_type"),
            sa_cast(owner_id, String).label("owner_id"),
            sa_cast(status, String).label("status"),
            sa_cast(identity, String).label("identity"),
        )
        if from_models:
            query = query.select_from(*from_models)
        return query.where(predicate)


workline_repository = WorkLineRepository()


__all__ = ["WorkLineRepository", "workline_repository"]
