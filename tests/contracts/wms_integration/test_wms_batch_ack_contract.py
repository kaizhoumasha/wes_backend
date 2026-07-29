"""E12/E13 ACK、冻结成员与 terminal result 的闭合集成合同。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"
E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


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
    from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE

    operation = WMS_OPERATION_BY_IDENTITY[E13]
    binding = next(binding for binding in WMS_PROVIDER_PROFILE.bindings if binding.operation.identity == E13)

    assert operation.max_candidate_count == 12
    assert binding.max_candidate_count == operation.max_candidate_count
    operation.request_model.model_validate(_e13_payload_with_candidate_count(operation.max_candidate_count))
    with pytest.raises(ValidationError, match="max_candidate_count"):
        operation.request_model.model_validate(_e13_payload_with_candidate_count(operation.max_candidate_count + 1))


def test_e13_request_binds_digest_to_ordered_frozen_candidates() -> None:
    operation = WMS_OPERATION_BY_IDENTITY[E13]
    payload = _e13_payload()

    operation.request_model.model_validate(payload)

    forged = deepcopy(payload)
    forged["candidate_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="candidate_digest"):
        operation.request_model.model_validate(forged)

    reordered = deepcopy(payload)
    reordered["candidate_items"] = list(reversed(reordered["candidate_items"]))
    with pytest.raises(ValidationError, match="candidate_digest"):
        operation.request_model.model_validate(reordered)


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
    request = operation.request_model.model_validate(payload)
    ack = WmsEffectAck.model_validate(build_typed_ack(operation_identity, "idem-batch", payload))
    result = operation.result_model.model_validate(
        build_typed_result(
            operation_identity,
            payload,
            source_version=3,
            completed_at="2026-07-29T00:00:03+00:00",
            provider_reference=ack.provider_reference,
        )
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
    ack = WmsEffectAck.model_validate(build_typed_ack(operation_identity, "idem-batch", request_payload))
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
    ("operation_identity", "payload_factory"),
    [(E12, _e12_payload), (E13, _e13_payload)],
)
def test_batch_terminal_result_rejects_provider_reference_drift(operation_identity: str, payload_factory) -> None:
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck, validate_batch_terminal_result
    from tests.mock.wms_northbound_contract import build_typed_ack, build_typed_result

    request_payload = payload_factory()
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    request = operation.request_model.model_validate(request_payload)
    ack = WmsEffectAck.model_validate(build_typed_ack(operation_identity, "idem-reference", request_payload))
    result_payload = build_typed_result(
        operation_identity,
        request_payload,
        source_version=3,
        completed_at="2026-07-29T00:00:03+00:00",
    )
    result_payload["provider_reference"] = "provider-reference-drift"
    result = operation.result_model.model_validate(result_payload)

    with pytest.raises(ValueError, match="provider_reference"):
        validate_batch_terminal_result(request, ack, result)


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
    ack = WmsEffectAck.model_validate(build_typed_ack(operation_identity, "idem-reference", request_payload))
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
