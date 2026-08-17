from __future__ import annotations

import hashlib
import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app.execution.models import InboundEvidenceApplyStatus, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services import (
    InboundEvidenceAcceptance,
    WmsBusinessWaitFollowUp,
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationService,
)
from src.app.wms_adapter.client import WmsClient
from src.app.wms_adapter.inbound_adapter import InboundDispatchCode, WmsInboundAdapter, WmsInboundBusinessWaitPlanner
from src.app.wms_adapter.inbound_wire import (
    ADMISSION_OPERATION,
    MAX_INBOUND_BODY_BYTES,
    NG_PLACEMENT_OPERATION,
    PLACEMENT_OPERATION,
)
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpFailureKind, OutboundHttpResult

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
OTHER_OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4473"


class _Transport:
    def __init__(self, response: OutboundHttpResult) -> None:
        self.response = response
        self.requests = []

    async def send(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.response

    async def aclose(self) -> None:
        return None


def _request(operation: str = ADMISSION_OPERATION) -> dict[str, object]:
    if operation == NG_PLACEMENT_OPERATION:
        data = {
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "ng_evidence_id": "EVIDENCE-1",
            "ng_position": {"type": "NG_POSITION", "location_code": "NG-1"},
            "reason_code": "BUSINESS_REJECT",
            "business_context": "ROUGH_SORT_INBOUND",
        }
    elif operation == PLACEMENT_OPERATION:
        data = {
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "pkg_id": "PKG-1",
            "inbound_admission_id": "ADM-1",
            "target_assignment_id": "TARGET-1",
            "target_position": {
                "type": "ONE_LAYER_BIN_CELL",
                "rack_id": "RACK-1",
                "rack_slot_code": "SLOT-1",
                "bin_id": "BIN-1",
                "bin_cell_id": "CELL-1",
            },
            "placement_sequence": 1,
            "command_code": "CMD-1",
            "placed_at": 1,
        }
    else:
        data = {
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "six_in_one": {
                "LotCode": "LOT",
                "DateCode": "DATE",
                "Qty": "1",
                "ProductNo": "PN",
                "MfrPN": "MFR",
                "PONumber": "PO",
            },
            "measurements": {"diameter_mm": "1.000", "thickness_mm": "0.500"},
            "shape_result": "PASS",
            "line_run_epoch_id": "EPOCH-1",
            "workline_code": "WL-1",
            "source_position": {"type": "HANDOFF_POSITION", "location_code": "IN-1"},
        }
    return {"operation_id": OPERATION_ID, "operation": operation, "timestamp": 1, "data": data}


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _response(body: dict[str, object], *, status: int = 200) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status,
        response_headers=(("Content-Type", "application/json; charset=utf-8"),),
        decoded_body=json.dumps(body, separators=(",", ":")).encode(),
    )


@pytest.mark.asyncio
async def test_adapter_sends_decision_through_wms_client_and_returns_typed_evidence() -> None:
    payload = _request()
    transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            }
        )
    )

    result = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is InboundDispatchCode.DETERMINATE
    assert result.response_result == "ACCEPT"
    assert result.normalized_response == {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    assert transport.requests[0].path == "/api/v1/wes/decisions"


@pytest.mark.asyncio
async def test_fact_uses_fact_path_and_recorded_is_determinate() -> None:
    payload = _request(PLACEMENT_OPERATION)
    transport = _Transport(_response({"operation_id": OPERATION_ID, "code": "RECORDED", "timestamp": 2, "data": {}}))

    result = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=PLACEMENT_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert (result.code, result.response_result) == (InboundDispatchCode.DETERMINATE, "RECORDED")
    assert transport.requests[0].path == "/api/v1/wes/facts"


@pytest.mark.asyncio
async def test_ng_fact_without_pkg_id_omits_the_optional_field_on_the_wire() -> None:
    payload = _request(NG_PLACEMENT_OPERATION)
    transport = _Transport(_response({"operation_id": OPERATION_ID, "code": "RECORDED", "timestamp": 2, "data": {}}))

    result = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=NG_PLACEMENT_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is InboundDispatchCode.DETERMINATE
    sent = json.loads(transport.requests[0].body)
    assert "pkg_id" not in sent["data"]


@pytest.mark.asyncio
async def test_device_text_is_bounded_by_the_encoded_request_body_not_a_field_cap() -> None:
    payload = _request()
    encoded_one = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    lot_length = len("LOT") + MAX_INBOUND_BODY_BYTES - len(encoded_one)
    payload["data"]["six_in_one"]["LotCode"] = "x" * lot_length  # type: ignore[index]
    response = {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    exact_transport = _Transport(_response(response))

    exact = await WmsInboundAdapter(WmsClient(exact_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert exact.code is InboundDispatchCode.DETERMINATE
    assert len(exact_transport.requests[0].body) == MAX_INBOUND_BODY_BYTES

    payload["data"]["six_in_one"]["LotCode"] += "x"  # type: ignore[index, operator]
    oversized_transport = _Transport(_response(response))
    oversized = await WmsInboundAdapter(WmsClient(oversized_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert oversized.code is InboundDispatchCode.RECONCILING
    assert oversized_transport.requests == []


@pytest.mark.asyncio
async def test_busy_is_retryable_but_invalid_or_conflicting_response_fails_closed() -> None:
    payload = _request()
    busy_transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "BUSY",
                "timestamp": 2,
                "data": {"retry_after_ms": 250},
            },
            status=429,
        )
    )
    busy = await WmsInboundAdapter(WmsClient(busy_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert (busy.code, busy.retry_after_ms) == (InboundDispatchCode.RETRY, 250)

    conflict_transport = _Transport(
        _response(
            {
                "operation_id": OPERATION_ID,
                "code": "CONFLICT",
                "timestamp": 2,
                "data": {"reason_code": "IDEMPOTENCY_CONFLICT"},
            },
            status=409,
        )
    )
    conflict = await WmsInboundAdapter(WmsClient(conflict_transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert conflict.code is InboundDispatchCode.RECONCILING


@pytest.mark.asyncio
async def test_delivery_unknown_and_request_identity_mismatch_never_become_business_wait() -> None:
    payload = _request()
    transport = _Transport(
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
            failure_kind=OutboundHttpFailureKind.READ_TIMEOUT,
        )
    )
    unknown = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )
    assert unknown.code is InboundDispatchCode.DELIVERY_UNKNOWN

    mismatch = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest="0" * 64,
    )
    assert mismatch.code is InboundDispatchCode.RECONCILING
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_preassociation_bad_request_response_fails_closed_without_retry() -> None:
    payload = _request()
    transport = _Transport(_response({}, status=400))
    transport.response = OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=400,
        decoded_body=b"",
    )

    result = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is InboundDispatchCode.RECONCILING


@pytest.mark.asyncio
async def test_response_with_wrong_operation_id_is_reconciling_with_response_evidence() -> None:
    payload = _request()
    response_body = {
        "operation_id": OTHER_OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    transport = _Transport(_response(response_body))

    result = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is InboundDispatchCode.RECONCILING
    assert result.normalized_response == response_body


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["INVALID_DTO", "INVALID_HEADER", "INVALID_JSON", "INVALID_STATUS"])
async def test_received_invalid_http_response_is_reconciling_and_never_retryable(case: str) -> None:
    payload = _request()
    valid_body = {
        "operation_id": OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    body = valid_body
    status = 200
    headers = (("Content-Type", "application/json; charset=utf-8"),)
    decoded_body = json.dumps(body, separators=(",", ":")).encode()
    if case == "INVALID_DTO":
        body = {**valid_body, "data": {"result": "ACCEPT"}}
        decoded_body = json.dumps(body, separators=(",", ":")).encode()
    elif case == "INVALID_HEADER":
        headers = (("Content-Type", "text/plain"),)
    elif case == "INVALID_JSON":
        decoded_body = b"{"
    else:
        status = 418
    transport = _Transport(
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=status,
            response_headers=headers,
            decoded_body=decoded_body,
        )
    )

    result = await WmsInboundAdapter(WmsClient(transport)).dispatch(
        operation=ADMISSION_OPERATION,
        operation_id=OPERATION_ID,
        request_payload=payload,
        request_digest=_digest(payload),
    )

    assert result.code is InboundDispatchCode.RECONCILING
    assert result.normalized_response == (None if case == "INVALID_JSON" else body)


class _Transaction(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        return None


class _Sessions:
    def begin(self) -> _Transaction:
        return _Transaction()


class _ConfirmationRepository:
    def __init__(self, confirmations: list[WmsConfirmation]) -> None:
        self.confirmations = confirmations

    async def lock_identity(self, db, operation, operation_id):  # type: ignore[no-untyped-def]
        return None

    async def get_by_identity_for_update(self, db, operation, operation_id):  # type: ignore[no-untyped-def]
        return next(
            (
                confirmation
                for confirmation in self.confirmations
                if confirmation.operation == operation and confirmation.operation_id == operation_id
            ),
            None,
        )

    async def add(self, db, confirmation):  # type: ignore[no-untyped-def]
        confirmation.id = max((item.id or 0 for item in self.confirmations), default=0) + 1
        self.confirmations.append(confirmation)
        return confirmation

    async def claim_eligible(self, db, *, now, claim_token, claim_expires_at, limit):  # type: ignore[no-untyped-def]
        claimed = [
            confirmation
            for confirmation in self.confirmations
            if (
                confirmation.status == WmsConfirmationStatus.PENDING
                and (confirmation.next_attempt_at is None or confirmation.next_attempt_at <= now)
            )
            or (
                confirmation.status == WmsConfirmationStatus.DISPATCHING
                and confirmation.claim_expires_at is not None
                and confirmation.claim_expires_at <= now
            )
        ][:limit]
        for confirmation in claimed:
            confirmation.status = WmsConfirmationStatus.DISPATCHING
            confirmation.claim_token = claim_token
            confirmation.claimed_at = now
            confirmation.claim_expires_at = claim_expires_at
            confirmation.attempt_count += 1
        return claimed

    async def get_claimed_for_update(self, db, confirmation_id, claim_token):  # type: ignore[no-untyped-def]
        return next(
            (
                confirmation
                for confirmation in self.confirmations
                if confirmation.id == confirmation_id
                and confirmation.status == WmsConfirmationStatus.DISPATCHING
                and confirmation.claim_token == claim_token
            ),
            None,
        )

    async def flush(self, db):  # type: ignore[no-untyped-def]
        return None


class _EvidenceService:
    def __init__(self) -> None:
        self.calls = []

    async def accept(self, db, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return InboundEvidenceAcceptance(SimpleNamespace(id=501, received_at=kwargs["received_at"]), duplicate=False)


class _Adapter:
    def __init__(self, result) -> None:  # type: ignore[no-untyped-def]
        self.result = result
        self.calls = []

    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.result


class _BusinessWaitPlanner:
    def __init__(self, follow_up: WmsBusinessWaitFollowUp | None) -> None:
        self.follow_up = follow_up
        self.calls = []

    def plan(self, confirmation, response_payload):  # type: ignore[no-untyped-def]
        self.calls.append((confirmation, response_payload))
        return self.follow_up


class _ConflictDuringDispatchAdapter:
    def __init__(self, conflict_service: WmsConfirmationService, confirmation: WmsConfirmation, now: datetime) -> None:
        self._conflict_service = conflict_service
        self._confirmation = confirmation
        self._now = now

    async def dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        conflicting_payload = json.loads(json.dumps(kwargs["request_payload"]))
        conflicting_payload["data"]["workline_code"] = "CONFLICT-DURING-HTTP"
        conflict = await self._conflict_service.create_or_get(
            object(),
            operation=kwargs["operation"],
            operation_id=kwargs["operation_id"],
            material_execution_id=self._confirmation.material_execution_id,
            request_payload=conflicting_payload,
            deadline_at=self._confirmation.deadline_at,
            created_at=self._now,
        )
        assert isinstance(conflict, WmsConfirmationIdentityConflictResult)
        return SimpleNamespace(
            code=InboundDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": kwargs["operation_id"],
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            },
            response_result="ACCEPT",
            retry_after_ms=None,
        )


def _confirmation(identifier: int, now: datetime) -> WmsConfirmation:
    payload = _request()
    return WmsConfirmation(
        id=identifier,
        operation=ADMISSION_OPERATION,
        operation_id=f"019f12d0-58d7-7b4d-a23a-{identifier:012x}",
        material_execution_id=21,
        request_digest=_digest(payload),
        request_payload={**payload, "operation_id": f"019f12d0-58d7-7b4d-a23a-{identifier:012x}"},
        deadline_at=now + timedelta(minutes=5),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_confirmation_dispatch_batch_is_bounded_and_completes_only_after_response_evidence() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmations = [_confirmation(identifier, now) for identifier in range(1, 102)]
    for confirmation in confirmations:
        confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository(confirmations)
    evidence = _EvidenceService()
    adapter = _Adapter(
        SimpleNamespace(
            code=InboundDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": OPERATION_ID,
                "code": "DECIDED",
                "timestamp": 2,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            },
            response_result="ACCEPT",
            retry_after_ms=None,
        )
    )
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=adapter,
        evidence_service=evidence,  # type: ignore[arg-type]
    )

    processed = await service.dispatch_batch(limit=101, now=now)

    assert processed == 100
    assert len(adapter.calls) == len(evidence.calls) == 100
    assert all(call["apply_status"] is InboundEvidenceApplyStatus.APPLIED for call in evidence.calls)
    assert all(confirmation.status == WmsConfirmationStatus.COMPLETED for confirmation in confirmations[:100])
    assert confirmations[100].status == WmsConfirmationStatus.PENDING


@pytest.mark.asyncio
async def test_confirmation_dispatch_rechecks_deadline_and_delivery_unknown_reuses_identity() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    expired = _confirmation(1, now)
    expired.deadline_at = now
    retryable = _confirmation(2, now)
    retryable.request_digest = _digest(retryable.request_payload)
    repository = _ConfirmationRepository([expired, retryable])
    adapter = _Adapter(
        SimpleNamespace(
            code=InboundDispatchCode.DELIVERY_UNKNOWN,
            normalized_response=None,
            response_result=None,
            retry_after_ms=None,
        )
    )
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=adapter,
        evidence_service=_EvidenceService(),  # type: ignore[arg-type]
    )

    processed = await service.dispatch_batch(limit=2, now=now)

    assert processed == 2
    assert expired.status == WmsConfirmationStatus.RECONCILING
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["operation_id"] == retryable.operation_id
    assert retryable.status == WmsConfirmationStatus.PENDING
    assert retryable.retry_eligible is True
    assert retryable.next_attempt_at == now + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_business_wait_completes_original_and_atomically_creates_due_follow_up() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    response_body = {
        "operation_id": confirmation.operation_id,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "WAIT", "reason_code": "CELL_PENDING", "retry_after_ms": 250},
    }
    follow_up_payload = {**confirmation.request_payload, "operation_id": OTHER_OPERATION_ID, "timestamp": 3}
    planner = _BusinessWaitPlanner(
        WmsBusinessWaitFollowUp(
            operation=confirmation.operation,
            operation_id=OTHER_OPERATION_ID,
            request_payload=follow_up_payload,
            next_attempt_at=now + timedelta(milliseconds=250),
        )
    )
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=InboundDispatchCode.DETERMINATE,
                normalized_response=response_body,
                response_result="WAIT",
                retry_after_ms=250,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
        business_wait_planner=planner,
    )

    assert await service.dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.COMPLETED
    assert confirmation.response_evidence_id == 501
    follow_up = repository.confirmations[-1]
    assert follow_up is not confirmation
    assert follow_up.status == WmsConfirmationStatus.PENDING
    assert follow_up.operation_id != confirmation.operation_id
    assert follow_up.next_attempt_at == now + timedelta(milliseconds=250)
    assert len(evidence.calls) == len(planner.calls) == 1
    assert await service.dispatch_batch(now=now) == 0

    service._adapter = _Adapter(
        SimpleNamespace(
            code=InboundDispatchCode.DETERMINATE,
            normalized_response={
                "operation_id": follow_up.operation_id,
                "code": "DECIDED",
                "timestamp": 4,
                "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
            },
            response_result="ACCEPT",
            retry_after_ms=None,
        )
    )
    assert await service.dispatch_batch(now=follow_up.next_attempt_at) == 1
    assert follow_up.status == WmsConfirmationStatus.COMPLETED
    assert follow_up.attempt_count == 1


def test_wms_business_wait_planner_uses_new_identity_timestamp_and_received_time() -> None:
    received_at = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, received_at)
    confirmation.completed_at = received_at
    planner = WmsInboundBusinessWaitPlanner(
        operation_id_factory=lambda: OTHER_OPERATION_ID,
    )

    follow_up = planner.plan(
        confirmation,
        {
            "operation_id": confirmation.operation_id,
            "code": "DECIDED",
            "timestamp": 2,
            "data": {"result": "WAIT", "reason_code": "CELL_PENDING", "retry_after_ms": 250},
        },
    )

    assert follow_up is not None
    assert follow_up.operation_id == OTHER_OPERATION_ID
    assert follow_up.request_payload["operation_id"] == OTHER_OPERATION_ID
    assert follow_up.request_payload["timestamp"] == int(received_at.timestamp() * 1000)
    assert follow_up.next_attempt_at == received_at + timedelta(milliseconds=250)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_case",
    [
        "identity-format",
        "identity-overlong",
        "missing-field",
        "cross-execution",
        "cross-trace",
        "extra-business-field",
    ],
)
async def test_invalid_business_wait_follow_up_keeps_evidence_and_fails_closed(invalid_case: str) -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    response_body = {
        "operation_id": confirmation.operation_id,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "WAIT", "reason_code": "CELL_PENDING", "retry_after_ms": 250},
    }
    follow_up_operation_id = OTHER_OPERATION_ID
    follow_up_payload = json.loads(json.dumps(confirmation.request_payload))
    follow_up_payload["operation_id"] = follow_up_operation_id
    follow_up_payload["timestamp"] = 3
    if invalid_case == "identity-format":
        follow_up_operation_id = "not-a-uuid"
        follow_up_payload["operation_id"] = follow_up_operation_id
    elif invalid_case == "identity-overlong":
        follow_up_operation_id = "7" * 161
        follow_up_payload["operation_id"] = follow_up_operation_id
    elif invalid_case == "missing-field":
        del follow_up_payload["data"]["workline_code"]
    elif invalid_case == "cross-execution":
        follow_up_payload["data"]["material_execution_id"] = "EXEC-OTHER"
    elif invalid_case == "cross-trace":
        follow_up_payload["data"]["material_trace_id"] = "TRACE-OTHER"
    else:
        follow_up_payload["data"]["unexpected_business_field"] = "MUST_NOT_CHANGE"
    invalid = WmsBusinessWaitFollowUp(
        operation=confirmation.operation,
        operation_id=follow_up_operation_id,
        request_payload=follow_up_payload,
        next_attempt_at=now + timedelta(milliseconds=250),
    )
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=InboundDispatchCode.DETERMINATE,
                normalized_response=response_body,
                response_result="WAIT",
                retry_after_ms=250,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
        business_wait_planner=_BusinessWaitPlanner(invalid),
    )

    assert await service.dispatch_batch(now=now) == 1
    assert len(evidence.calls) == 1
    assert repository.confirmations == [confirmation]
    assert confirmation.response_evidence_id == 501
    assert confirmation.response_result == "WAIT"
    assert confirmation.status == WmsConfirmationStatus.RECONCILING


@pytest.mark.asyncio
async def test_confirmation_persists_received_json_object_before_marking_reconciling() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    response_body = {
        "operation_id": OTHER_OPERATION_ID,
        "code": "DECIDED",
        "timestamp": 2,
        "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
    }
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_Adapter(
            SimpleNamespace(
                code=InboundDispatchCode.RECONCILING,
                normalized_response=response_body,
                response_result=None,
                retry_after_ms=None,
            )
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert [call["normalized_payload"] for call in evidence.calls] == [response_body]


@pytest.mark.asyncio
async def test_inflight_identity_conflict_fences_late_response_in_fast_dispatch() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)
    confirmation = _confirmation(1, now)
    confirmation.request_digest = _digest(confirmation.request_payload)
    repository = _ConfirmationRepository([confirmation])
    evidence = _EvidenceService()
    service = WmsConfirmationService(
        repository=repository,
        session_factory=_Sessions(),  # type: ignore[arg-type]
        adapter=_ConflictDuringDispatchAdapter(
            WmsConfirmationService(repository=repository),
            confirmation,
            now,
        ),
        evidence_service=evidence,  # type: ignore[arg-type]
    )

    assert await service.dispatch_batch(now=now) == 1
    assert confirmation.status == WmsConfirmationStatus.RECONCILING
    assert confirmation.response_evidence_id is None
    assert confirmation.claim_token is None
    assert confirmation.claimed_at is None
    assert confirmation.claim_expires_at is None
    assert evidence.calls == []

    confirmation.claim_token = "stale-late-response"
    assert await repository.get_claimed_for_update(object(), confirmation.id, "stale-late-response") is None
