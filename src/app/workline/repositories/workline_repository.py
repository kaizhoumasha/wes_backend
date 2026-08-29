"""WorkLine Repository 层"""

from typing import Any, cast

from sqlalchemy import String, and_, exists, literal, or_, select, true, union_all
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
from src.app.transport.contracts import TransportTaskStatus
from src.app.transport.models import TransportTask
from src.app.workline.models import WorkLine
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
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
