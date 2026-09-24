"""非可选 JSON 合同不得在 PostgreSQL schema 中退化为 nullable。"""

from __future__ import annotations

from src.app.device.models import Device, DeviceCommand
from src.app.execution.models import InboundEvidence, InboundEvidenceConflict, WmsConfirmation
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.workline.models import WorkLine


def test_non_optional_json_columns_are_not_nullable() -> None:
    columns = (
        DeviceCommand.__table__.c.params,
        Device.__table__.c.diagnostic_profile,
        InboundEvidenceConflict.__table__.c.normalized_payload,
        InboundEvidence.__table__.c.normalized_payload,
        WmsConfirmation.__table__.c.request_payload,
        WorkLine.__table__.c.diagnostic_profile,
        WorkLine.__table__.c.runtime_config_json,
        WorkLinePosition.__table__.c.metadata_json,
    )

    assert {f"{column.table.name}.{column.name}" for column in columns if column.nullable} == set()
