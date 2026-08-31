"""非可选 JSON 合同不得在 PostgreSQL schema 中退化为 nullable。"""

from __future__ import annotations

from src.app.device.models import Device, DeviceCommand, DeviceStatusObservation
from src.app.execution.models import InboundEvidence, InboundEvidenceConflict, WmsConfirmation
from src.app.resource.models import (
    Bin,
    BinPlacement,
    BinSlotTemplate,
    BinType,
    Rack,
    RackSlotTemplate,
    RackType,
    ResourceStateEvent,
)
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.app.workline.models import WorkLine
from src.app.workline.models.safety import WorklineSafetyIncident


def test_non_optional_json_columns_are_not_nullable() -> None:
    columns = (
        DeviceCommand.__table__.c.params,
        DeviceStatusObservation.__table__.c.raw_payload,
        Device.__table__.c.diagnostic_profile,
        InboundEvidenceConflict.__table__.c.normalized_payload,
        InboundEvidence.__table__.c.normalized_payload,
        BinPlacement.__table__.c.metadata_json,
        BinSlotTemplate.__table__.c.metadata_json,
        BinType.__table__.c.metadata_json,
        Bin.__table__.c.metadata_json,
        RackSlotTemplate.__table__.c.allowed_bin_types,
        RackSlotTemplate.__table__.c.allowed_material_carrier_types,
        RackType.__table__.c.metadata_json,
        Rack.__table__.c.metadata_json,
        ResourceStateEvent.__table__.c.payload_json,
        WmsConfirmation.__table__.c.request_payload,
        WorkLine.__table__.c.diagnostic_profile,
        WorkLine.__table__.c.runtime_config_json,
        WorklineRackPosition.__table__.c.metadata_json,
        WorklineSafetyIncident.__table__.c.drain_error_json,
        WorklineSafetyIncident.__table__.c.evidence_json,
        WorklineSafetyIncident.__table__.c.missing_identifiers,
        WorklineSafetyIncident.__table__.c.recovery_check_json,
        WorklineSafetyIncident.__table__.c.release_evidence_json,
        WorklineSafetyIncident.__table__.c.resolution_inputs_tried,
        WorklineSafetyIncident.__table__.c.trigger_payload_json,
    )

    assert {f"{column.table.name}.{column.name}" for column in columns if column.nullable} == set()
