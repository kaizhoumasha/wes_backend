"""跨表与证据 ID 在 PostgreSQL 中必须保持 BIGINT。"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from src.app.device.models.command import DeviceCommand
from src.app.execution.models.inbound_evidence import InboundEvidence
from src.app.execution.models.transport_decision_binding import TransportDecisionBinding
from src.app.execution.models.wms_confirmation import WmsConfirmation
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition


def test_reference_and_evidence_ids_compile_to_postgresql_bigint() -> None:
    columns = (
        DeviceCommand.__table__.c.workline_id,
        DeviceCommand.__table__.c.material_execution_id,
        InboundEvidence.__table__.c.material_execution_id,
        WmsConfirmation.__table__.c.material_execution_id,
        WorklineSession.__table__.c.workline_id,
        WorklineTimeline.__table__.c.session_id,
        WorklineTimeline.__table__.c.workline_id,
        WorklineTimeline.__table__.c.related_command_id,
        TransportDecisionBinding.__table__.c.workline_id,
        WorkLinePosition.__table__.c.workline_id,
    )

    assert {
        f"{column.table.name}.{column.name}": column.type.compile(dialect=postgresql.dialect()).upper()
        for column in columns
    } == {f"{column.table.name}.{column.name}": "BIGINT" for column in columns}
