"""Release operational readiness 的四账本只读聚合查询。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Select, and_, func, not_, or_, select, text, true

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceApplyStatus
from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.app.transport.contracts import TransportTaskStatus
from src.app.transport.models import TransportTask

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

POSTGRESQL_STATEMENT_TIMEOUT = "10s"


@dataclass(frozen=True)
class ReleaseOperationalReadinessCountSnapshot:
    """四账本单快照的分类计数。"""

    device_command_wait_drain: int
    device_command_block: int
    device_command_unknown: int
    device_command_invalid: int
    transport_task_wait_drain: int
    transport_task_block: int
    transport_task_unknown: int
    transport_task_invalid: int
    inbound_evidence_wait_drain: int
    inbound_evidence_block: int
    inbound_evidence_unknown: int
    inbound_evidence_invalid: int
    wms_confirmation_wait_drain: int
    wms_confirmation_block: int
    wms_confirmation_unknown: int
    wms_confirmation_invalid: int


def _conditional_count(predicate: object, label: str):
    return func.count().filter(predicate).label(label)


class ReleaseOperationalReadinessRepository:
    """以一个 PostgreSQL SELECT 聚合四个可靠账本，不加载业务行。"""

    @staticmethod
    def build_statement() -> Select[tuple[object, ...]]:
        device_statuses = tuple(status.value for status in CommandStatus)
        device_wait_statuses = (
            CommandStatus.PENDING.value,
            CommandStatus.DISPATCHING.value,
            CommandStatus.ACKNOWLEDGED.value,
        )
        device_terminal_statuses = (
            CommandStatus.SUCCEEDED.value,
            CommandStatus.FAILED.value,
            CommandStatus.TIMED_OUT.value,
        )
        device_known = DeviceCommand.status.in_(device_statuses)
        device_invalid = and_(
            device_known,
            or_(
                and_(
                    DeviceCommand.status.in_((*device_wait_statuses, CommandStatus.RECONCILING.value)),
                    DeviceCommand.completed_at.is_not(None),
                ),
                and_(
                    DeviceCommand.status.in_(device_terminal_statuses),
                    DeviceCommand.completed_at.is_(None),
                ),
            ),
        )
        device = (
            select(
                _conditional_count(
                    and_(DeviceCommand.status.in_(device_wait_statuses), not_(device_invalid)),
                    "device_command_wait_drain",
                ),
                _conditional_count(
                    and_(DeviceCommand.status == CommandStatus.RECONCILING.value, not_(device_invalid)),
                    "device_command_block",
                ),
                _conditional_count(not_(device_known), "device_command_unknown"),
                _conditional_count(device_invalid, "device_command_invalid"),
            )
            .select_from(DeviceCommand)
            .subquery()
        )

        transport_statuses = tuple(status.value for status in TransportTaskStatus)
        transport_known = TransportTask.status.in_(transport_statuses)
        transport_gap = TransportTask.outcome_version > TransportTask.published_outcome_version
        transport_invalid = and_(
            transport_known,
            or_(
                TransportTask.outcome_version < 0,
                TransportTask.published_outcome_version < 0,
                TransportTask.published_outcome_version > TransportTask.outcome_version,
                and_(
                    transport_gap,
                    or_(
                        TransportTask.outcome_json.is_(None),
                        func.json_typeof(TransportTask.outcome_json) == "null",
                    ),
                ),
            ),
        )
        transport = (
            select(
                _conditional_count(
                    and_(
                        transport_known,
                        not_(transport_invalid),
                        TransportTask.status != TransportTaskStatus.RECONCILING.value,
                        or_(
                            TransportTask.status.in_(
                                (TransportTaskStatus.PENDING.value, TransportTaskStatus.ACCEPTED.value)
                            ),
                            transport_gap,
                        ),
                    ),
                    "transport_task_wait_drain",
                ),
                _conditional_count(
                    and_(
                        TransportTask.status == TransportTaskStatus.RECONCILING.value,
                        not_(transport_invalid),
                    ),
                    "transport_task_block",
                ),
                _conditional_count(not_(transport_known), "transport_task_unknown"),
                _conditional_count(transport_invalid, "transport_task_invalid"),
            )
            .select_from(TransportTask)
            .subquery()
        )

        inbound_statuses = tuple(status.value for status in InboundEvidenceApplyStatus)
        inbound_known = InboundEvidence.apply_status.in_(inbound_statuses)
        claim_identity_incomplete = or_(
            and_(
                InboundEvidence.decision_claim_token.is_(None), InboundEvidence.decision_claim_expires_at.is_not(None)
            ),
            and_(
                InboundEvidence.decision_claim_token.is_not(None), InboundEvidence.decision_claim_expires_at.is_(None)
            ),
        )
        inbound_invalid = and_(
            inbound_known,
            or_(
                claim_identity_incomplete,
                and_(
                    InboundEvidence.published_at.is_not(None),
                    or_(
                        InboundEvidence.decision_digest.is_(None),
                        InboundEvidence.decision_claim_token.is_not(None),
                        InboundEvidence.decision_claim_expires_at.is_not(None),
                    ),
                ),
            ),
        )
        inbound_claimable = and_(
            InboundEvidence.apply_status == InboundEvidenceApplyStatus.APPLIED.value,
            InboundEvidence.published_at.is_(None),
            not_(
                and_(
                    InboundEvidence.kind == "DEVICE_RESULT",
                    InboundEvidence.material_execution_id.is_(None),
                )
            ),
        )
        inbound = (
            select(
                _conditional_count(
                    and_(
                        not_(inbound_invalid),
                        or_(
                            InboundEvidence.apply_status == InboundEvidenceApplyStatus.PENDING.value,
                            inbound_claimable,
                        ),
                    ),
                    "inbound_evidence_wait_drain",
                ),
                _conditional_count(
                    and_(
                        InboundEvidence.apply_status == InboundEvidenceApplyStatus.RECONCILING.value,
                        not_(inbound_invalid),
                    ),
                    "inbound_evidence_block",
                ),
                _conditional_count(not_(inbound_known), "inbound_evidence_unknown"),
                _conditional_count(inbound_invalid, "inbound_evidence_invalid"),
            )
            .select_from(InboundEvidence)
            .subquery()
        )

        wms_statuses = tuple(status.value for status in WmsConfirmationStatus)
        wms_known = WmsConfirmation.status.in_(wms_statuses)
        wms_invalid = and_(
            wms_known,
            or_(
                and_(
                    WmsConfirmation.status == WmsConfirmationStatus.COMPLETED.value,
                    WmsConfirmation.completed_at.is_(None),
                ),
                and_(
                    WmsConfirmation.status != WmsConfirmationStatus.COMPLETED.value,
                    WmsConfirmation.completed_at.is_not(None),
                ),
            ),
        )
        wms = (
            select(
                _conditional_count(
                    and_(
                        WmsConfirmation.status.in_(
                            (WmsConfirmationStatus.PENDING.value, WmsConfirmationStatus.DISPATCHING.value)
                        ),
                        not_(wms_invalid),
                    ),
                    "wms_confirmation_wait_drain",
                ),
                _conditional_count(
                    and_(
                        WmsConfirmation.status == WmsConfirmationStatus.RECONCILING.value,
                        not_(wms_invalid),
                    ),
                    "wms_confirmation_block",
                ),
                _conditional_count(not_(wms_known), "wms_confirmation_unknown"),
                _conditional_count(wms_invalid, "wms_confirmation_invalid"),
            )
            .select_from(WmsConfirmation)
            .subquery()
        )

        aggregate_snapshot = device.join(transport, true()).join(inbound, true()).join(wms, true())
        return select(*device.c, *transport.c, *inbound.c, *wms.c).select_from(aggregate_snapshot)

    async def load_counts(self, db: AsyncSession) -> ReleaseOperationalReadinessCountSnapshot:
        await db.execute(text(f"SET LOCAL statement_timeout = '{POSTGRESQL_STATEMENT_TIMEOUT}'"))
        row = (await db.execute(self.build_statement())).one()._mapping
        return ReleaseOperationalReadinessCountSnapshot(
            **{field: int(row[field]) for field in ReleaseOperationalReadinessCountSnapshot.__dataclass_fields__}
        )


__all__ = [
    "ReleaseOperationalReadinessCountSnapshot",
    "ReleaseOperationalReadinessRepository",
]
