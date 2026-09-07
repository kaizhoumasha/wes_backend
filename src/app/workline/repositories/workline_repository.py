"""WorkLine Repository 层"""

from typing import Any, cast

from sqlalchemy import String, and_, case, func, literal, or_, select, union_all
from sqlalchemy import cast as sa_cast
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.command import CommandStatus, DeviceCommand
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
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
from src.app.workline.activation import WorkLineDeviceBinding, WorkLinePositionBinding
from src.app.workline.models.safety import WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.app.workline.models.workline import WorkLine
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

    async def set_active_for_start(self, db: AsyncSession, workline: WorkLine) -> WorkLine:
        """在 START 调用方事务内把已锁定 WorkLine 标记为活动。"""

        workline.is_active = True
        workline.increment_version()
        await db.flush()
        return workline

    async def set_inactive_for_deactivate(self, db: AsyncSession, workline: WorkLine) -> WorkLine:
        """在停用调用方事务内把已锁定 WorkLine 标记为停用。"""

        workline.is_active = False
        workline.increment_version()
        await db.flush()
        return workline

    async def list_active_plugin_identities(self, db: AsyncSession) -> list[tuple[str, str]]:
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(
            select(columns.plugin_key, columns.plugin_version)
            .where(columns.is_active.is_(True), columns.is_deleted.is_(False))
            .distinct()
        )
        return list(result.tuples())

    async def list_bindings(self, db: AsyncSession, workline_id: int) -> list[WorkLineDeviceBinding]:
        line = await self.get_for_update(db, workline_id)
        if line is None:
            return []
        contracts = line.device_contracts
        return [
            WorkLineDeviceBinding(workline_id=workline_id, device_role=role, device_code=code, **contracts[code])
            for role, code in sorted(line.config.get("device_bindings", {}).items())
            if code in contracts
        ]

    async def list_position_bindings(self, db: AsyncSession, workline_id: int) -> list[WorkLinePositionBinding]:
        line = await self.get_for_update(db, workline_id)
        if line is None:
            return []
        return [
            WorkLinePositionBinding(position_role=role, **position)
            for role, position in sorted(line.position_bindings.items())
        ]

    async def get_binding_for_command_creation(
        self, db: AsyncSession, *, workline_id: int, device_code: str
    ) -> WorkLineDeviceBinding | None:
        line = await self.get_for_update(db, workline_id)
        if line is None or not line.is_active:
            return None
        return next(
            (binding for binding in await self.list_bindings(db, workline_id) if binding.device_code == device_code),
            None,
        )

    async def list_bindings_by_role_for_update(
        self, db: AsyncSession, *, workline_id: int, device_role: str
    ) -> list[WorkLineDeviceBinding]:
        return [binding for binding in await self.list_bindings(db, workline_id) if binding.device_role == device_role]

    async def get_binding_by_role_and_code_for_update(
        self, db: AsyncSession, *, workline_id: int, device_role: str, device_code: str
    ) -> WorkLineDeviceBinding | None:
        return next(
            (
                binding
                for binding in await self.list_bindings_by_role_for_update(
                    db, workline_id=workline_id, device_role=device_role
                )
                if binding.device_code == device_code
            ),
            None,
        )

    async def get_active_binding_for_device(self, db: AsyncSession, device_code: str) -> WorkLineDeviceBinding | None:
        from src.app.device.models.device import Device

        devices = cast("Any", Device).__table__.c
        workline_id = await db.scalar(
            select(devices.work_line_id).where(devices.device_code == device_code, devices.is_deleted.is_(False))
        )
        if workline_id is None:
            return None
        return await self.get_binding_for_command_creation(db, workline_id=workline_id, device_code=device_code)

    async def get_unfinished_workload_summary(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> dict[str, Any]:
        """返回未闭合可靠义务与本线实际占用的数量和稳定样本。"""

        workline = cast("Any", WorkLine).__table__.c
        material = cast("Any", MaterialExecution).__table__.c
        command = cast("Any", DeviceCommand).__table__.c
        transport = cast("Any", TransportTask).__table__.c
        evidence = cast("Any", InboundEvidence).__table__.c
        confirmation = cast("Any", WmsConfirmation).__table__.c
        picking = cast("Any", PickingTask).__table__.c

        predicates = {
            "material_executions": and_(
                material.workline_id == workline_id,
                material.status != MaterialExecutionStatus.CLOSED,
            ),
            "transport_tasks": and_(
                transport.authority_workline_id == workline_id,
                or_(
                    transport.status.in_(
                        (TransportTaskStatus.PENDING, TransportTaskStatus.ACCEPTED, TransportTaskStatus.RECONCILING)
                    ),
                    transport.outcome_version > transport.published_outcome_version,
                ),
            ),
        }
        unclosed_command = and_(
            workline.id == workline_id,
            command.workline_id == workline.id,
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
            workline.id == workline_id,
            evidence.workline_id == workline.id,
            or_(
                evidence.apply_status == InboundEvidenceApplyStatus.PENDING,
                evidence.apply_status == InboundEvidenceApplyStatus.RECONCILING,
                and_(
                    evidence.apply_status == InboundEvidenceApplyStatus.APPLIED,
                    evidence.published_at.is_(None),
                    ~and_(
                        evidence.material_execution_id.is_(None),
                        or_(
                            evidence.kind == InboundEvidenceKind.DEVICE_RESULT,
                            and_(
                                evidence.kind == InboundEvidenceKind.WMS_RESULT,
                                evidence.id.in_(
                                    select(confirmation.response_evidence_id).where(
                                        confirmation.workline_id == workline.id,
                                        confirmation.status == WmsConfirmationStatus.COMPLETED,
                                    )
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
        unfinished_confirmation = and_(
            confirmation.status != WmsConfirmationStatus.COMPLETED,
            or_(
                confirmation.material_execution_id.in_(select(material.id).where(material.workline_id == workline_id)),
                confirmation.picking_task_id.in_(select(picking.id).where(picking.workline_id == workline_id)),
                confirmation.workline_id.in_(select(workline.id).where(workline.id == workline_id)),
            ),
        )
        # prepare 的确认完成不等于 PickingTask 闭合；计划阻塞也不能因任务阶段改变而解除围栏。
        unfinished_picking = and_(
            picking.workline_id == workline_id,
            or_(
                picking.status.in_((PickingTaskStatus.PREPARING, PickingTaskStatus.EXECUTING)),
                picking.plan_blocked_evidence_id.is_not(None),
            ),
        )

        owner_union = union_all(
            self._sample_query(
                2,
                "material_executions",
                "material_execution",
                material.id,
                material.status,
                material.execution_code,
                predicates["material_executions"],
            ),
            self._sample_query(
                4,
                "device_commands",
                "device_command",
                command.id,
                command.status,
                command.command_code,
                unclosed_command,
                from_models=(DeviceCommand, WorkLine),
            ),
            self._sample_query(
                5,
                "transport_tasks",
                "transport_task",
                transport.id,
                transport.status,
                transport.transport_task_id,
                predicates["transport_tasks"],
            ),
            self._sample_query(
                6,
                "inbound_evidences",
                "inbound_evidence",
                evidence.id,
                evidence.apply_status,
                evidence.source_identity,
                blocking_evidence,
                from_models=(InboundEvidence, WorkLine),
            ),
            self._sample_query(
                7,
                "wms_confirmations",
                "wms_confirmation",
                confirmation.id,
                confirmation.status,
                confirmation.operation_id,
                unfinished_confirmation,
                from_models=(WmsConfirmation,),
            ),
            self._sample_query(
                8,
                "picking_tasks",
                "picking_task",
                picking.id,
                picking.status,
                picking.task_id,
                unfinished_picking,
            ),
        ).subquery("unfinished_owner_candidates")
        ranked = select(
            owner_union,
            func.count().over(partition_by=owner_union.c.owner_key).label("owner_count"),
            func.row_number()
            .over(partition_by=owner_union.c.owner_key, order_by=owner_union.c.owner_id)
            .label("sample_rank"),
        ).subquery("unfinished_owner_ranked")
        statement = (
            select(
                ranked.c.owner_order,
                ranked.c.owner_key,
                ranked.c.owner_type,
                ranked.c.owner_count,
                ranked.c.owner_id,
                ranked.c.status,
                ranked.c.identity,
            )
            .where(ranked.c.sample_rank == 1)
            .order_by(ranked.c.owner_order)
        )
        rows = (await db.execute(statement)).all()
        owner_keys = (
            "material_executions",
            "device_commands",
            "transport_tasks",
            "inbound_evidences",
            "wms_confirmations",
            "picking_tasks",
        )
        by_type = dict.fromkeys(owner_keys, 0)
        samples: dict[str, dict[str, str]] = {}
        for row in rows:
            by_type[row.owner_key] = int(row.owner_count)
            samples[row.owner_key] = {
                "type": row.owner_type,
                "id": str(row.owner_id),
                "status": row.status,
                "identity": row.identity,
            }
        from src.app.execution.repositories.position_projection_repository import position_projection_repository

        positions = await position_projection_repository.get_active_workline_summary(db, workline_id)
        by_type["position_projections"] = positions["count"]
        if positions["sample"] is not None:
            samples["position_projections"] = positions["sample"]
        return {
            "count": sum(by_type.values()),
            "sample": next(iter(samples.values()), None),
            "samples": samples,
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

        workline = cast("Any", WorkLine).__table__.c
        material = cast("Any", MaterialExecution).__table__.c
        command = cast("Any", DeviceCommand).__table__.c
        transport = cast("Any", TransportTask).__table__.c
        confirmation = cast("Any", WmsConfirmation).__table__.c
        incident = cast("Any", WorklineSafetyIncident).__table__.c
        bin_placement = cast("Any", BinPlacement).__table__.c
        rack_placement = cast("Any", RackPlacement).__table__.c

        target_rows = union_all(
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
                "DEVICE_COMMAND",
                command.command_code,
                "DEVICE_COMMAND",
                command.device_code,
                literal("device_command:") + sa_cast(command.id, String),
                workline.id == workline_id,
                command.workline_id == workline.id,
                command.status.in_(
                    (
                        CommandStatus.PENDING,
                        CommandStatus.DISPATCHING,
                        CommandStatus.ACKNOWLEDGED,
                        CommandStatus.RECONCILING,
                    )
                ),
                from_models=(DeviceCommand, WorkLine),
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
                "WMS_CONFIRMATION",
                confirmation.operation_id,
                "WMS_CONFIRMATION",
                confirmation.operation,
                literal("wms_confirmation:") + sa_cast(confirmation.id, String),
                workline.id == workline_id,
                confirmation.workline_id == workline.id,
                confirmation.status != WmsConfirmationStatus.COMPLETED,
                from_models=(WmsConfirmation, WorkLine),
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
        owner_key: str,
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
            literal(owner_key).label("owner_key"),
            literal(owner_type).label("owner_type"),
            owner_id.label("owner_id"),
            sa_cast(status, String).label("status"),
            sa_cast(identity, String).label("identity"),
        )
        if from_models:
            query = query.select_from(*from_models)
        return query.where(predicate)


workline_repository = WorkLineRepository()


__all__ = ["WorkLineRepository", "workline_repository"]
