from __future__ import annotations

import pytest
from wes_plugin_sdk import (
    CreateTransportTask,
    TransportLeg,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    TransportTaskType,
    TransportZonePosition,
)


def _decision(
    source: object,
    target: object,
    *,
    face: object = "90",
    template: object = TransportRcsTemplateId.CTU01,
    rack_id: str = "rack-1",
    leg: TransportLeg = TransportLeg.NEW_IN,
) -> CreateTransportTask:
    return CreateTransportTask(
        material_execution_id="execution-1",
        fact_id="fact-1",
        task_type=TransportTaskType.RACK_MOVE,
        rack_replacement_id="replacement-1",
        leg=leg,
        current_rack_id="rack-old",
        rack_id=rack_id,
        source=source,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        target_face=face,  # type: ignore[arg-type]
        rcs_template_id=template,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("source", "target", "template"),
    [
        (TransportZonePosition("zone-1"), TransportRackPosition("work"), TransportRcsTemplateId.CTU01),
        (TransportRackReference("rack-1"), TransportRackPosition("work"), TransportRcsTemplateId.CTU01),
        (TransportRackPosition("a"), TransportRackPosition("b"), TransportRcsTemplateId.CTU01),
        (TransportRackPosition("a"), TransportRackReference("rack-1"), TransportRcsTemplateId.CTU03),
        (TransportRackPosition("a"), TransportZonePosition("zone-1"), TransportRcsTemplateId.CTU03),
        (TransportRackPosition("a"), TransportRackPosition("b"), TransportRcsTemplateId.CTU03),
        (TransportRackPosition("a"), TransportRackPosition("b"), TransportRcsTemplateId.F01),
    ],
)
def test_sdk_accepts_only_approved_rack_move_edges(source: object, target: object, template: object) -> None:
    decision = _decision(source, target, template=template)

    assert decision.source is source
    assert decision.target is target
    assert decision.rcs_template_id is template


@pytest.mark.parametrize("face", ["90", "270", "FACE@01", "面-1", " ", "\x00", "x" * 1000])
def test_sdk_preserves_any_non_empty_face_string(face: str) -> None:
    assert _decision(TransportZonePosition("zone-1"), TransportRackPosition("work"), face=face).target_face == face


@pytest.mark.parametrize("face", ["", None, 90, True])
def test_sdk_rejects_empty_or_non_string_face(face: object) -> None:
    with pytest.raises((TypeError, ValueError), match="target_face"):
        _decision(TransportZonePosition("zone-1"), TransportRackPosition("work"), face=face)


@pytest.mark.parametrize(
    ("source", "target", "template"),
    [
        (TransportRackReference("rack-1"), TransportZonePosition("zone-1"), TransportRcsTemplateId.CTU01),
        (TransportZonePosition("zone-1"), TransportRackReference("rack-1"), TransportRcsTemplateId.CTU03),
        (TransportRackPosition("a"), TransportRackPosition("b"), TransportRcsTemplateId.CTU02),
    ],
)
def test_sdk_rejects_unapproved_rack_move_edges(source: object, target: object, template: object) -> None:
    with pytest.raises(ValueError, match="approved edge"):
        _decision(source, target, template=template)


def test_sdk_requires_rack_reference_identity_to_match_outer_rack() -> None:
    with pytest.raises(ValueError, match="RACK location_code"):
        _decision(TransportRackReference("other-rack"), TransportRackPosition("work"))
