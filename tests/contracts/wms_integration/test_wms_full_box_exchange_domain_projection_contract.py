"""E11 满箱交换领域投影的静态合同 RED。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import ForeignKeyConstraint

from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffDemand
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.app.wms_integration.ports.effect_status import (
    WmsEffectStatusRequest,
    parse_wms_effect_status_snapshot,
)
from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E08 = "wms.fulfillment.request_rack_supply@v1"
E09 = "wms.fulfillment.request_rack_transport@v1"
E11 = "wms.fulfillment.full_box_exchange@v1"
E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"


def _operation(identity: str) -> Any:
    return next(operation for operation in EFFECT_OPERATIONS if operation.identity == identity)


def test_e11_declares_its_own_domain_projection_kind() -> None:
    projected = {
        operation.identity: operation.domain_projection_kind
        for operation in EFFECT_OPERATIONS
        if operation.domain_projection_kind is not None
    }

    assert set(projected) == {E08, E09, E11, E12}
    assert projected[E11].value == "FULL_BOX_EXCHANGE_DEMAND"


def test_e11_parent_demand_persists_stage_gate_and_active_intent_fields() -> None:
    expected_fields = {
        "full_box_exchange_station_code",
        "full_box_exchange_rack_face",
        "active_full_box_exchange_intent_id",
    }

    assert expected_fields <= set(SmtInboundHandoffDemand.model_fields)
    assert SmtInboundHandoffDemand.__table__.columns["full_box_exchange_station_code"].type.length == 120
    assert SmtInboundHandoffDemand.model_fields["active_full_box_exchange_intent_id"].default is None


def test_e11_parent_demand_active_intent_references_runtime_intent() -> None:
    foreign_keys = {
        tuple(element.parent.name for element in constraint.elements): tuple(
            element.target_fullname for element in constraint.elements
        )
        for constraint in SmtInboundHandoffDemand.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert foreign_keys[("active_full_box_exchange_intent_id",)] == ("wes_runtime.runtime_intent_logs.id",)


def test_e11_parent_demand_active_intent_is_indexed() -> None:
    indexed_columns = {
        tuple(column.name for column in index.columns) for index in SmtInboundHandoffDemand.__table__.indexes
    }

    assert ("active_full_box_exchange_intent_id",) in indexed_columns


def test_e11_request_freezes_only_wes_authoritative_source_facts() -> None:
    fields = set(_operation(E11).request_model.model_fields)
    forbidden_wms_selection_fields = {
        "selected_empty_box_id",
        "empty_box_id",
        "full_box_destination",
        "empty_box_destination",
        "target_rack_id",
        "target_slot_id",
    }

    assert {
        "station_code",
        "rack_face",
        "rack_id",
        "full_box_id",
        "source_slot_id",
        "occupancies",
    } <= fields
    assert fields.isdisjoint(forbidden_wms_selection_fields)


def test_e11_request_rejects_duplicate_frozen_occupancy_members() -> None:
    operation = _operation(E11)
    payload = dict(REQUEST_FIXTURES[E11])
    payload["occupancies"] = [
        *payload["occupancies"],
        dict(payload["occupancies"][0]),
    ]

    with pytest.raises(ValidationError, match="occup"):
        operation.request_model.model_validate(payload)


def test_e11_request_allows_multiple_pkgs_in_the_same_occupancy() -> None:
    operation = _operation(E11)
    payload = dict(REQUEST_FIXTURES[E11])
    second_pkg = {
        **payload["occupancies"][0],
        "pkg_id": "PKG-OTHER",
    }
    payload["occupancies"] = [*payload["occupancies"], second_pkg]

    request = operation.request_model.model_validate(payload)

    assert [(item.occupancy_id, item.pkg_id) for item in request.occupancies] == [
        ("OCC-001", "PKG-001"),
        ("OCC-001", "PKG-OTHER"),
    ]


def test_e11_request_rejects_duplicate_pkg_across_different_occupancies() -> None:
    operation = _operation(E11)
    payload = dict(REQUEST_FIXTURES[E11])
    duplicate_pkg = {
        **payload["occupancies"][0],
        "occupancy_id": "OCC-OTHER",
    }
    payload["occupancies"] = [*payload["occupancies"], duplicate_pkg]

    with pytest.raises(ValidationError, match="pkg"):
        operation.request_model.model_validate(payload)


def test_e11_terminal_result_keeps_empty_box_at_the_frozen_source_slot() -> None:
    operation = _operation(E11)
    request = operation.request_model.model_validate(REQUEST_FIXTURES[E11])
    result_payload = {
        "dispatch_key": request.dispatch_key,
        "provider_reference": "provider-e11",
        "source_version": "2",
        "exchange_request_key": request.exchange_request_key,
        "full_box_id": request.full_box_id,
        "selected_empty_box_id": "EMPTY-1",
        "full_box_destination": {
            "rack_id": "FIVE-RACK-1",
            "bin_id": request.full_box_id,
            "slot_id": "FIVE-SLOT-1",
        },
        "empty_box_destination": {
            "rack_id": request.rack_id,
            "bin_id": "EMPTY-1",
            "slot_id": "WRONG-SOURCE-SLOT",
        },
        "final_relations": [
            {
                "rack_id": "FIVE-RACK-1",
                "bin_id": request.full_box_id,
                "slot_id": "FIVE-SLOT-1",
            },
            {
                "rack_id": request.rack_id,
                "bin_id": "EMPTY-1",
                "slot_id": "WRONG-SOURCE-SLOT",
            },
        ],
        "task_outcome": "SUCCESS",
        "inventory_source_version": "2",
    }

    status_request = WmsEffectStatusRequest(
        operation_identity=E11,
        idempotency_key="idem-e11",
        request_payload=request.model_dump(mode="json"),
        frozen_ack=WmsEffectAck(
            operation_identity=E11,
            idempotency_key="idem-e11",
            provider_reference="provider-e11",
            submission_state="ACCEPTED",
        ),
    )
    with pytest.raises(ValueError, match=r"source_slot|empty_box_destination"):
        parse_wms_effect_status_snapshot(
            request=status_request,
            raw_response={
                "state": "COMPLETED",
                "provider_reference": "provider-e11",
                "accepted_scope": None,
                "reason_code": None,
                "updated_at": "2026-07-30T08:00:00+00:00",
                "source_version": 2,
                "result_payload": result_payload,
            },
        )


@pytest.mark.parametrize("conflict", ["same_bin", "same_destination"])
def test_e11_terminal_result_rejects_self_exchange_and_shared_destination(conflict: str) -> None:
    operation = _operation(E11)
    request = operation.request_model.model_validate(REQUEST_FIXTURES[E11])
    empty_bin_id = request.full_box_id if conflict == "same_bin" else "EMPTY-1"
    full_destination = {
        "rack_id": request.rack_id if conflict == "same_destination" else "FIVE-RACK-1",
        "bin_id": request.full_box_id,
        "slot_id": request.source_slot_id if conflict == "same_destination" else "FIVE-SLOT-1",
    }
    empty_destination = {
        "rack_id": request.rack_id,
        "bin_id": empty_bin_id,
        "slot_id": request.source_slot_id,
    }

    with pytest.raises(ValidationError, match=r"selected_empty|destination|coordinate"):
        operation.result_model.model_validate(
            {
                "dispatch_key": request.dispatch_key,
                "provider_reference": "provider-e11",
                "source_version": "2",
                "exchange_request_key": request.exchange_request_key,
                "full_box_id": request.full_box_id,
                "selected_empty_box_id": empty_bin_id,
                "full_box_destination": full_destination,
                "empty_box_destination": empty_destination,
                "final_relations": [full_destination, empty_destination],
                "task_outcome": "SUCCESS",
                "inventory_source_version": "2",
            }
        )
