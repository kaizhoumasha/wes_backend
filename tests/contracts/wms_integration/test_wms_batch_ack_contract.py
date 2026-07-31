"""E12/E13 ACK、冻结成员与 terminal result 的闭合集成合同。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"
E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


def _validate_json(model, payload):
    return model.model_validate_json(json.dumps(payload))


def _candidate_digest(payload: dict[str, object]) -> str:
    canonical = {
        "workline_id": payload["workline_id"],
        "queue_code": payload["queue_code"],
        "candidate_items": [
            {
                "sequence_no": item["sequence_no"],
                "route_instance_id": item["route_instance_id"],
                "bin_id": item["bin_id"],
                "scan3_enqueued_at": item["scan3_enqueued_at"],
                "queue_position": item["queue_position"],
            }
            for item in payload["candidate_items"]
        ],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _e12_payload() -> dict[str, object]:
    payload = deepcopy(REQUEST_FIXTURES[E12])
    first = payload["items"][0]
    payload["items"] = [
        first,
        {
            **first,
            "sequence_no": 2,
            "route_instance_id": "ROUTE-002",
            "bin_id": "BIN-002",
            "source_slot_id": "SLOT-002",
            "reserved_queue_position": 2,
        },
        {
            **first,
            "sequence_no": 3,
            "route_instance_id": "ROUTE-003",
            "bin_id": "BIN-003",
            "source_slot_id": "SLOT-003",
            "reserved_queue_position": 3,
        },
    ]
    return payload


def _e13_payload() -> dict[str, object]:
    payload = deepcopy(REQUEST_FIXTURES[E13])
    first = payload["candidate_items"][0]
    payload["candidate_items"] = [
        first,
        {
            **first,
            "sequence_no": 2,
            "route_instance_id": "ROUTE-002",
            "bin_id": "BIN-002",
            "scan3_enqueued_at": "2026-07-29T00:00:01+00:00",
            "queue_position": 2,
        },
        {
            **first,
            "sequence_no": 3,
            "route_instance_id": "ROUTE-003",
            "bin_id": "BIN-003",
            "scan3_enqueued_at": "2026-07-29T00:00:02+00:00",
            "queue_position": 3,
        },
    ]
    payload["candidate_digest"] = _candidate_digest(payload)
    return payload


def _e13_payload_with_candidate_count(candidate_count: int) -> dict[str, object]:
    payload = deepcopy(REQUEST_FIXTURES[E13])
    first = payload["candidate_items"][0]
    payload["candidate_items"] = [
        {
            **first,
            "sequence_no": index,
            "route_instance_id": f"ROUTE-{index:03d}",
            "bin_id": f"BIN-{index:03d}",
            "scan3_enqueued_at": f"2026-07-29T00:00:{index - 1:02d}+00:00",
            "queue_position": index,
        }
        for index in range(1, candidate_count + 1)
    ]
    payload["candidate_digest"] = _candidate_digest(payload)
    return payload


def test_e13_candidate_window_is_bounded_by_unique_definition_and_provider_binding() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]
    catalog = build_provider_catalog()
    binding = next(binding for binding in catalog.bindings if binding.operation.identity == E13)

    assert operation.max_candidate_count == 12
    assert binding.max_candidate_count == operation.max_candidate_count
    _validate_json(operation.request_model, _e13_payload_with_candidate_count(operation.max_candidate_count))
    with pytest.raises(ValidationError, match="max_candidate_count"):
        _validate_json(operation.request_model, _e13_payload_with_candidate_count(operation.max_candidate_count + 1))


def test_e13_request_binds_digest_to_ordered_frozen_candidates() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]
    payload = _e13_payload()

    _validate_json(operation.request_model, payload)

    forged = deepcopy(payload)
    forged["candidate_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="candidate_digest"):
        _validate_json(operation.request_model, forged)

    reordered = deepcopy(payload)
    reordered["candidate_items"] = list(reversed(reordered["candidate_items"]))
    with pytest.raises(ValidationError, match="candidate_digest"):
        _validate_json(operation.request_model, reordered)


def test_accepted_scope_rejects_duplicate_members_and_invalid_digest_shape() -> None:
    from src.app.wms_integration.ports.fulfillment_operations import WmsAcceptedScope, accepted_scope_digest

    with pytest.raises(ValidationError, match="duplicate"):
        WmsAcceptedScope(
            object_keys=("BIN-001", "BIN-001"),
            scope_digest=accepted_scope_digest(("BIN-001", "BIN-001")),
        )
    with pytest.raises(ValidationError, match="scope_digest"):
        WmsAcceptedScope(object_keys=("BIN-001",), scope_digest="not-a-digest")


@pytest.mark.parametrize("operation_identity", [E12, E13])
def test_batch_requests_reject_duplicate_members(operation_identity: str) -> None:
    payload = _e12_payload() if operation_identity == E12 else _e13_payload()
    member_field = "items" if operation_identity == E12 else "candidate_items"
    payload[member_field][1]["bin_id"] = payload[member_field][0]["bin_id"]

    with pytest.raises(ValidationError, match="duplicate"):
        WMS_OPERATION_BY_IDENTITY[operation_identity].request_model.model_validate(payload)


def test_e13_ack_requires_ordered_candidate_prefix_and_matching_digest() -> None:
    from src.app.wms_integration.ports.fulfillment_operations import (
        WmsAcceptedScope,
        WmsEffectAck,
        accepted_scope_digest,
        validate_fulfillment_ack,
    )

    request = WMS_OPERATION_BY_IDENTITY[E13].request_model.model_validate(_e13_payload())
    valid_scope = WmsAcceptedScope(
        object_keys=("BIN-001", "BIN-002"),
        scope_digest=accepted_scope_digest(("BIN-001", "BIN-002")),
    )
    valid_ack = WmsEffectAck(
        operation_identity=E13,
        idempotency_key="idem-e13",
        provider_reference="provider-e13",
        submission_state="ACCEPTED",
        accepted_scope=valid_scope,
    )
    assert validate_fulfillment_ack(request, valid_ack) is valid_ack

    non_prefix = valid_ack.model_copy(
        update={
            "accepted_scope": WmsAcceptedScope(
                object_keys=("BIN-001", "BIN-003"),
                scope_digest=accepted_scope_digest(("BIN-001", "BIN-003")),
            )
        }
    )
    with pytest.raises(ValueError, match="ordered prefix"):
        validate_fulfillment_ack(request, non_prefix)

    wrong_digest = valid_ack.model_copy(
        update={
            "accepted_scope": WmsAcceptedScope(
                object_keys=("BIN-001", "BIN-002"),
                scope_digest="f" * 64,
            )
        }
    )
    with pytest.raises(ValueError, match="scope digest"):
        validate_fulfillment_ack(request, wrong_digest)


def test_e12_ack_must_accept_the_entire_frozen_batch() -> None:
    from src.app.wms_integration.ports.fulfillment_operations import (
        WmsAcceptedScope,
        WmsEffectAck,
        accepted_scope_digest,
        validate_fulfillment_ack,
    )

    request = WMS_OPERATION_BY_IDENTITY[E12].request_model.model_validate(_e12_payload())
    partial_keys = ("BIN-001", "BIN-002")
    partial_ack = WmsEffectAck(
        operation_identity=E12,
        idempotency_key="idem-e12",
        provider_reference="provider-e12",
        submission_state="ACCEPTED",
        accepted_scope=WmsAcceptedScope(
            object_keys=partial_keys,
            scope_digest=accepted_scope_digest(partial_keys),
        ),
    )

    with pytest.raises(ValueError, match="entire frozen batch"):
        validate_fulfillment_ack(request, partial_ack)


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory"),
    [(E12, _e12_payload), (E13, _e13_payload)],
)
def test_mock_multi_member_ack_and_terminal_result_preserve_frozen_correspondence(
    operation_identity: str,
    payload_factory,
) -> None:
    from src.app.wms_integration.ports.fulfillment_operations import (
        WmsEffectAck,
        validate_batch_terminal_result,
        validate_fulfillment_ack,
    )
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    payload = payload_factory()
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    request = _validate_json(operation.request_model, payload)
    ack = WmsEffectAck.model_validate(
        build_typed_ack(operation_identity, "idem-batch", payload, submission_state="ACCEPTED")
    )
    result = _validate_json(
        operation.result_model,
        build_typed_result(
            operation_identity,
            payload,
            source_version=3,
            completed_at="2026-07-29T00:00:03+00:00",
            provider_reference=ack.provider_reference,
        ),
    )

    validate_fulfillment_ack(request, ack)
    assert validate_batch_terminal_result(request, ack, result) is result
    accepted_keys = ack.accepted_scope.object_keys
    assert tuple(item.bin_id for item in result.items) == accepted_keys
    assert tuple(item.route_instance_id for item in result.items) == tuple(
        item.route_instance_id for item in (request.items if operation_identity == E12 else request.candidate_items)
    )


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory"),
    [(E12, _e12_payload), (E13, _e13_payload)],
)
def test_real_status_parser_requires_frozen_batch_ack_and_rejects_member_drift(
    operation_identity: str,
    payload_factory,
) -> None:
    from src.app.wms_integration.ports.effect_status import (
        WmsBatchEffectStatusRequest,
        WmsEffectStatusRequest,
        parse_wms_effect_status_snapshot,
    )
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    request_payload = payload_factory()
    ack = WmsEffectAck.model_validate(
        build_typed_ack(operation_identity, "idem-batch", request_payload, submission_state="ACCEPTED")
    )
    request = WmsBatchEffectStatusRequest(
        operation_identity=operation_identity,
        idempotency_key="idem-batch",
        request_payload=request_payload,
        frozen_ack=ack,
    )
    result_payload = build_typed_result(
        operation_identity,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
        provider_reference=ack.provider_reference,
    )
    wire = {
        "state": "COMPLETED",
        "provider_reference": ack.provider_reference,
        "reason_code": None,
        "updated_at": "2026-07-29T00:00:03+00:00",
        "source_version": 3,
        "result_payload": result_payload,
    }

    snapshot = parse_wms_effect_status_snapshot(request=request, raw_response=wire)
    assert snapshot.result is not None

    with pytest.raises(ValidationError, match="batch"):
        WmsEffectStatusRequest(
            operation_identity=operation_identity,
            idempotency_key="idem-batch",
            request_payload=request_payload,
            frozen_ack=ack,
        )

    forged = deepcopy(wire)
    forged["result_payload"]["items"][0]["route_instance_id"] = "FORGED-ROUTE"
    with pytest.raises(ValueError, match="frozen request members"):
        parse_wms_effect_status_snapshot(request=request, raw_response=forged)

    reordered = deepcopy(wire)
    reordered["result_payload"]["items"] = list(reversed(reordered["result_payload"]["items"]))
    reordered["result_payload"]["accepted_object_keys"] = list(
        reversed(reordered["result_payload"]["accepted_object_keys"])
    )
    with pytest.raises((ValueError, ValidationError), match=r"ACK|frozen request members"):
        parse_wms_effect_status_snapshot(request=request, raw_response=reordered)


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory", "accepted_count"),
    [(E12, _e12_payload, 3), (E13, _e13_payload, 2)],
)
def test_pre_ack_batch_status_recovers_only_authored_exact_or_prefix_scope(
    operation_identity: str,
    payload_factory,
    accepted_count: int,
) -> None:
    from src.app.wms_integration.ports.effect_status import (
        WmsBatchEffectStatusRequest,
        parse_wms_effect_status_snapshot,
    )
    from src.app.wms_integration.ports.fulfillment_operations import accepted_scope_digest

    request_payload = payload_factory()
    members = request_payload["items"] if operation_identity == E12 else request_payload["candidate_items"]
    accepted_keys = tuple(item["bin_id"] for item in members[:accepted_count])
    request = WmsBatchEffectStatusRequest(
        operation_identity=operation_identity,
        idempotency_key="idem-status-first",
        request_payload=request_payload,
    )
    wire = {
        "state": "PROCESSING",
        "provider_reference": "provider-status-first",
        "accepted_scope": {
            "object_keys": accepted_keys,
            "scope_digest": accepted_scope_digest(accepted_keys),
        },
        "reason_code": None,
        "updated_at": "2026-07-29T00:00:03+00:00",
        "source_version": 1,
        "result_payload": None,
    }

    snapshot = parse_wms_effect_status_snapshot(request=request, raw_response=wire)

    assert snapshot.recovered_ack is not None
    assert snapshot.recovered_ack.accepted_scope is not None
    assert snapshot.recovered_ack.accepted_scope.object_keys == accepted_keys

    drifted_keys = ("BIN-001", "BIN-003") if operation_identity == E13 else accepted_keys[:-1]
    wire["accepted_scope"] = {
        "object_keys": drifted_keys,
        "scope_digest": accepted_scope_digest(drifted_keys),
    }
    with pytest.raises(ValueError, match=r"ordered prefix|entire frozen batch"):
        parse_wms_effect_status_snapshot(request=request, raw_response=wire)


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory"),
    [(E12, _e12_payload), (E13, _e13_payload)],
)
def test_pre_ack_batch_completed_status_cross_checks_terminal_scope(
    operation_identity: str,
    payload_factory,
) -> None:
    from src.app.wms_integration.ports.effect_status import (
        WmsBatchEffectStatusRequest,
        parse_wms_effect_status_snapshot,
    )
    from src.app.wms_integration.ports.fulfillment_operations import accepted_scope_digest
    from tests.mock.wms_northbound_contract import build_typed_result

    request_payload = payload_factory()
    result_payload = build_typed_result(
        operation_identity,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
        provider_reference="provider-status-first",
    )
    accepted_keys = tuple(result_payload["accepted_object_keys"])
    wire = {
        "state": "COMPLETED",
        "provider_reference": "provider-status-first",
        "accepted_scope": {
            "object_keys": accepted_keys,
            "scope_digest": accepted_scope_digest(accepted_keys),
        },
        "reason_code": None,
        "updated_at": "2026-07-29T00:00:03+00:00",
        "source_version": 3,
        "result_payload": result_payload,
    }
    request = WmsBatchEffectStatusRequest(
        operation_identity=operation_identity,
        idempotency_key="idem-status-first",
        request_payload=request_payload,
    )
    wire["result_payload"]["accepted_object_keys"] = list(reversed(accepted_keys))

    with pytest.raises((ValidationError, ValueError), match=r"result contract|accepted_object_keys|ACK"):
        parse_wms_effect_status_snapshot(request=request, raw_response=wire)


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory"),
    [(E12, _e12_payload), (E13, _e13_payload)],
)
def test_batch_terminal_result_rejects_provider_reference_drift(operation_identity: str, payload_factory) -> None:
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck, validate_batch_terminal_result
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    request_payload = payload_factory()
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    request = _validate_json(operation.request_model, request_payload)
    ack = WmsEffectAck.model_validate(
        build_typed_ack(operation_identity, "idem-reference", request_payload, submission_state="ACCEPTED")
    )
    result_payload = build_typed_result(
        operation_identity,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
    )
    result_payload["provider_reference"] = "provider-reference-drift"
    result = _validate_json(operation.result_model, result_payload)

    with pytest.raises(ValueError, match="provider_reference"):
        validate_batch_terminal_result(request, ack, result)


def test_e12_requires_contiguous_sequence_and_unique_reserved_queue_positions() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E12]
    payload = _e12_payload()
    payload["items"][1]["sequence_no"] = 3
    with pytest.raises(ValidationError, match="sequence"):
        _validate_json(operation.request_model, payload)

    payload = _e12_payload()
    payload["items"][1]["reserved_queue_position"] = payload["items"][0]["reserved_queue_position"]
    with pytest.raises(ValidationError, match="reserved_queue_position"):
        _validate_json(operation.request_model, payload)


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory", "items_field", "position_field"),
    [
        (E12, _e12_payload, "items", "reserved_queue_position"),
        (E13, _e13_payload, "candidate_items", "queue_position"),
    ],
)
def test_batch_request_queue_positions_are_strictly_positive(
    operation_identity: str,
    payload_factory,
    items_field: str,
    position_field: str,
) -> None:
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    payload = payload_factory()
    payload[items_field][0][position_field] = 0
    if operation_identity == E13:
        payload["candidate_digest"] = _candidate_digest(payload)

    with pytest.raises(ValidationError, match=position_field):
        _validate_json(operation.request_model, payload)


@pytest.mark.parametrize(
    "scan3_enqueued_at",
    [
        "2026-07-29T00:00:00",
        "2026-07-29T08:00:00+08:00",
        "2026-07-29T00:00:00Z",
        "2026-07-29 00:00:00+00:00",
    ],
)
def test_e13_scan3_enqueued_at_requires_canonical_rfc3339_utc(scan3_enqueued_at: str) -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]
    payload = _e13_payload()
    payload["candidate_items"][0]["scan3_enqueued_at"] = scan3_enqueued_at
    payload["candidate_digest"] = _candidate_digest(payload)

    with pytest.raises(ValidationError, match="scan3_enqueued_at"):
        _validate_json(operation.request_model, payload)


@pytest.mark.parametrize(
    "scan3_enqueued_at",
    [
        "2026-07-29T00:00:00+00:00",
        "2026-07-29T00:00:00.123456+00:00",
    ],
)
def test_e13_scan3_enqueued_at_accepts_canonical_rfc3339_utc(scan3_enqueued_at: str) -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]
    payload = _e13_payload()
    payload["candidate_items"][0]["scan3_enqueued_at"] = scan3_enqueued_at
    payload["candidate_digest"] = _candidate_digest(payload)

    request = _validate_json(operation.request_model, payload)

    assert request.candidate_items[0].scan3_enqueued_at == scan3_enqueued_at


def test_e13_requires_strict_fifo_order_even_when_digest_is_recomputed() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]
    payload = _e13_payload()
    payload["candidate_items"] = list(reversed(payload["candidate_items"]))
    payload["candidate_digest"] = _candidate_digest(payload)

    with pytest.raises(ValidationError, match="FIFO"):
        _validate_json(operation.request_model, payload)


@pytest.mark.parametrize(
    ("first_timestamp", "second_timestamp"),
    [
        ("2026-07-29T00:00:00.1+00:00", "2026-07-29T00:00:00.100000+00:00"),
        ("2026-07-29T00:00:00+00:00", "2026-07-29T00:00:00.000000+00:00"),
    ],
)
def test_e13_equivalent_instants_use_queue_position_as_fifo_tiebreaker(
    first_timestamp: str,
    second_timestamp: str,
) -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]
    payload = _e13_payload()
    payload["candidate_items"][0].update(
        {
            "scan3_enqueued_at": first_timestamp,
            "queue_position": 2,
        }
    )
    payload["candidate_items"][1].update(
        {
            "scan3_enqueued_at": second_timestamp,
            "queue_position": 1,
        }
    )
    payload["candidate_digest"] = _candidate_digest(payload)

    with pytest.raises(ValidationError, match="FIFO"):
        _validate_json(operation.request_model, payload)


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory", "unknown_location"),
    [
        (
            E12,
            _e12_payload,
            {"final_rack_id": None, "final_slot_id": None, "final_queue_position": None},
        ),
        (
            E13,
            _e13_payload,
            {"final_rack_id": None, "final_slot_id": None, "final_queue_position": None},
        ),
    ],
)
def test_batch_terminal_outcome_and_member_final_facts_form_one_closed_contract(
    operation_identity: str,
    payload_factory,
    unknown_location: dict[str, object],
) -> None:
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck, validate_batch_terminal_result
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    request_payload = payload_factory()
    request = _validate_json(operation.request_model, request_payload)
    ack = WmsEffectAck.model_validate(
        build_typed_ack(operation_identity, "idem-batch", request_payload, submission_state="ACCEPTED")
    )
    valid_payload = build_typed_result(
        operation_identity,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
        provider_reference=ack.provider_reference,
    )

    missing_success_fact = deepcopy(valid_payload)
    missing_success_fact["items"][0].update(unknown_location)
    with pytest.raises((ValidationError, ValueError), match="final"):
        result = _validate_json(operation.result_model, missing_success_fact)
        validate_batch_terminal_result(request, ack, result)

    inconsistent_task_outcome = deepcopy(valid_payload)
    inconsistent_task_outcome["items"][0].update({"item_outcome": "UNKNOWN", **unknown_location})
    inconsistent_task_outcome["task_outcome"] = "SUCCESS"
    with pytest.raises((ValidationError, ValueError), match="task_outcome"):
        result = _validate_json(operation.result_model, inconsistent_task_outcome)
        validate_batch_terminal_result(request, ack, result)

    known_failure = deepcopy(valid_payload)
    for item in known_failure["items"]:
        item["item_outcome"] = "FAILED"
    known_failure["task_outcome"] = "FAILED_AFTER_EXECUTION"
    result = _validate_json(operation.result_model, known_failure)
    assert validate_batch_terminal_result(request, ack, result) is result

    unknown_failure = deepcopy(valid_payload)
    for item in unknown_failure["items"]:
        item.update({"item_outcome": "UNKNOWN", **unknown_location})
    unknown_failure["task_outcome"] = "FAILED_AFTER_EXECUTION"
    result = _validate_json(operation.result_model, unknown_failure)
    assert validate_batch_terminal_result(request, ack, result) is result


def test_e12_terminal_queue_position_must_match_each_frozen_reservation() -> None:
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck, validate_batch_terminal_result
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    operation = WMS_OPERATION_BY_IDENTITY[E12]
    request_payload = _e12_payload()
    request = _validate_json(operation.request_model, request_payload)
    ack = WmsEffectAck.model_validate(
        build_typed_ack(E12, "idem-e12-position", request_payload, submission_state="ACCEPTED")
    )
    result_payload = build_typed_result(
        E12,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
        provider_reference=ack.provider_reference,
    )
    result_payload["items"][0]["final_queue_position"] = 99
    result = _validate_json(operation.result_model, result_payload)

    with pytest.raises(ValueError, match="reserved_queue_position"):
        validate_batch_terminal_result(request, ack, result)


def test_e13_terminal_known_members_require_unique_final_rack_slot_targets() -> None:
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck, validate_batch_terminal_result
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    operation = WMS_OPERATION_BY_IDENTITY[E13]
    request_payload = _e13_payload()
    request = _validate_json(operation.request_model, request_payload)
    ack = WmsEffectAck.model_validate(
        build_typed_ack(E13, "idem-e13-target", request_payload, submission_state="ACCEPTED")
    )
    result_payload = build_typed_result(
        E13,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
        provider_reference=ack.provider_reference,
    )
    result_payload["items"][1].update(
        {
            "item_outcome": "FAILED",
            "final_rack_id": result_payload["items"][0]["final_rack_id"],
            "final_slot_id": result_payload["items"][0]["final_slot_id"],
        }
    )
    result_payload["items"][2].update(
        {
            "item_outcome": "UNKNOWN",
            "final_rack_id": None,
            "final_slot_id": None,
        }
    )
    result_payload["task_outcome"] = "PARTIAL_FAILURE"
    result = _validate_json(operation.result_model, result_payload)

    with pytest.raises(ValueError, match="unique final rack/slot"):
        validate_batch_terminal_result(request, ack, result)


def test_e13_terminal_allows_unique_partial_and_unknown_member_facts() -> None:
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck, validate_batch_terminal_result
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    operation = WMS_OPERATION_BY_IDENTITY[E13]
    request_payload = _e13_payload()
    request = _validate_json(operation.request_model, request_payload)
    ack = WmsEffectAck.model_validate(
        build_typed_ack(E13, "idem-e13-partial", request_payload, submission_state="ACCEPTED")
    )
    result_payload = build_typed_result(
        E13,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
        provider_reference=ack.provider_reference,
    )
    result_payload["items"][1]["item_outcome"] = "FAILED"
    result_payload["items"][2].update(
        {
            "item_outcome": "UNKNOWN",
            "final_rack_id": None,
            "final_slot_id": None,
        }
    )
    result_payload["task_outcome"] = "PARTIAL_FAILURE"
    result = _validate_json(operation.result_model, result_payload)

    assert validate_batch_terminal_result(request, ack, result) is result


@pytest.mark.parametrize(
    ("operation_identity", "payload_factory"),
    [(E12, _e12_payload), (E13, _e13_payload)],
)
def test_batch_status_rejects_provider_reference_drift(operation_identity: str, payload_factory) -> None:
    from src.app.wms_integration.ports.effect_status import (
        WmsBatchEffectStatusRequest,
        parse_wms_effect_status_snapshot,
    )
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
    from tests.mock.wms_northbound_contract import build_typed_ack

    request_payload = payload_factory()
    ack = WmsEffectAck.model_validate(
        build_typed_ack(operation_identity, "idem-reference", request_payload, submission_state="ACCEPTED")
    )
    request = WmsBatchEffectStatusRequest(
        operation_identity=operation_identity,
        idempotency_key="idem-reference",
        request_payload=request_payload,
        frozen_ack=ack,
    )
    wire = {
        "state": "PROCESSING",
        "provider_reference": "provider-reference-drift",
        "reason_code": None,
        "updated_at": "2026-07-29T00:00:03+00:00",
        "source_version": 3,
        "result_payload": None,
    }

    with pytest.raises(ValueError, match="provider_reference"):
        parse_wms_effect_status_snapshot(request=request, raw_response=wire)
