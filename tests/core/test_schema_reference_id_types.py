"""跨表与证据 ID 在 PostgreSQL 中必须保持 BIGINT。"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from src.app.execution.models.rack_replacement_transport_binding import RackReplacementTransportBinding
from src.app.resource.models import (
    BinContentSnapshot,
    BinMaterialMount,
    BinPlacement,
    RackPlacement,
    ResourceStateEvent,
)
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding, LineRunEpochPositionBinding
from src.app.workline.models.safety import WorklineSafetyIncident


def test_reference_and_evidence_ids_compile_to_postgresql_bigint() -> None:
    columns = (
        LineRunEpochDeviceBinding.__table__.c.line_run_epoch_id,
        LineRunEpochPositionBinding.__table__.c.line_run_epoch_id,
        RackReplacementTransportBinding.__table__.c.line_run_epoch_id,
        BinContentSnapshot.__table__.c.source_session_id,
        BinMaterialMount.__table__.c.writeback_evidence_id,
        BinPlacement.__table__.c.workline_id,
        RackPlacement.__table__.c.workline_id,
        ResourceStateEvent.__table__.c.workline_id,
        WorklineRackPosition.__table__.c.workline_id,
        WorklineSafetyIncident.__table__.c.cleared_by,
        WorklineSafetyIncident.__table__.c.source_command_id,
        WorklineSafetyIncident.__table__.c.source_device_id,
        WorklineSafetyIncident.__table__.c.source_evidence_id,
        WorklineSafetyIncident.__table__.c.source_inbox_id,
        WorklineSafetyIncident.__table__.c.workline_id,
    )

    assert {
        f"{column.table.name}.{column.name}": column.type.compile(dialect=postgresql.dialect()).upper()
        for column in columns
    } == {f"{column.table.name}.{column.name}": "BIGINT" for column in columns}
