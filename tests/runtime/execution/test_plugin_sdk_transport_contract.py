from __future__ import annotations

import pytest
from wes_plugin_sdk import (
    CompleteExecution,
    CreateTransportTask,
    DeferExecution,
    PauseForReconciliation,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    TransportTaskType,
    TransportZonePosition,
    Wait,
)


def _decision(
    source: object,
    target: object,
    *,
    face: object = "90",
    template: object = TransportRcsTemplateId.CTU01,
    rack_id: str = "rack-1",
    correlation_id: str = "flow-1",
    step: str = "NEW_IN",
    resource_fence_id: str = "rack-old",
) -> CreateTransportTask:
    return CreateTransportTask(
        material_execution_id="execution-1",
        fact_id="fact-1",
        task_type=TransportTaskType.RACK_MOVE,
        correlation_id=correlation_id,
        step=step,
        resource_fence_id=resource_fence_id,
        rack_id=rack_id,
        source=source,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        target_face=face,  # type: ignore[arg-type]
        rcs_template_id=template,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("correlation_id", "x" * 161),
        ("step", "x" * 81),
        ("resource_fence_id", "x" * 161),
        ("correlation_id", "bad\x00value"),
        ("step", "bad\x00value"),
        ("resource_fence_id", "bad\x00value"),
    ),
)
def test_sdk_rejects_transport_binding_identity_that_cannot_be_persisted(field_name: str, value: str) -> None:
    kwargs = {field_name: value}

    with pytest.raises(ValueError, match=field_name):
        _decision(TransportZonePosition("zone-1"), TransportRackPosition("work"), **kwargs)


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


@pytest.mark.parametrize("face", ["90", "270", "FACE@01", "面-1", " ", "x" * 1000])
def test_sdk_preserves_any_non_empty_face_string(face: str) -> None:
    assert _decision(TransportZonePosition("zone-1"), TransportRackPosition("work"), face=face).target_face == face


@pytest.mark.parametrize("face", ["", "\x00", "\ud800", None, 90, True])
def test_sdk_rejects_invalid_face(face: object) -> None:
    with pytest.raises((TypeError, ValueError), match="target_face"):
        _decision(TransportZonePosition("zone-1"), TransportRackPosition("work"), face=face)


@pytest.mark.parametrize(
    ("affected_resource_ids", "expected_message"),
    [
        ((), "affected_resource_ids must not be empty"),
        (("rack-1", "rack-1"), "affected_resource_ids must not contain duplicates"),
        ((" ",), "affected_resource_ids must not be blank"),
    ],
)
def test_sdk_rejects_invalid_reconciliation_reference_tuples(
    affected_resource_ids: tuple[str, ...],
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        PauseForReconciliation(
            material_execution_id="execution-1",
            fact_id="fact-1",
            reason_code="POSITION_UNKNOWN",
            affected_resource_ids=affected_resource_ids,
        )


@pytest.mark.parametrize("decision_type", [Wait, DeferExecution, PauseForReconciliation, CompleteExecution])
@pytest.mark.parametrize("field_name", ["material_execution_id", "fact_id", "reason_code"])
def test_reasoned_execution_decisions_reject_blank_identity_fields(
    decision_type: type[Wait | DeferExecution | PauseForReconciliation | CompleteExecution],
    field_name: str,
) -> None:
    values: dict[str, object] = {
        "material_execution_id": "execution-1",
        "fact_id": "fact-1",
        "reason_code": "WAIT",
    }
    values[field_name] = " "
    if decision_type is PauseForReconciliation:
        values["affected_resource_ids"] = ("rack-1",)

    with pytest.raises(ValueError, match=field_name):
        decision_type(**values)  # type: ignore[arg-type]


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
