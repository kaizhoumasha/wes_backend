from __future__ import annotations

import json

import pytest

from src.app.wms_adapter.transport_event_handler import MAX_TRANSPORT_EVENT_BODY_BYTES, TransportEventHandler


class FakeRecorder:
    def __init__(self, code: str = "RECEIVED") -> None:
        self.code = code
        self.calls: list[dict[str, object]] = []
        self._first_acks: dict[tuple[str, str], dict[str, object]] = {}

    async def record_callback(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        operation_id = str(kwargs["operation_id"])
        operation = str(kwargs["operation"])
        key = operation, operation_id
        first_ack = self._first_acks.get(key)
        if first_ack is not None:
            return {**first_ack, "http_status": 200, "code": "DUPLICATE"}
        message = kwargs["message"]
        assert isinstance(message, dict)
        data = message.get("data")
        transport_task_id = data.get("transport_task_id") if isinstance(data, dict) else None
        if isinstance(transport_task_id, str):
            try:
                transport_task_id.encode("utf-8")
            except UnicodeEncodeError:
                transport_task_id = None
        rejection_reason_code = kwargs["rejection_reason_code"]
        if rejection_reason_code is not None:
            response_data = {"reason_code": rejection_reason_code}
            if isinstance(transport_task_id, str):
                response_data["transport_task_id"] = transport_task_id
            first_ack = {
                "http_status": 422,
                "code": "REJECTED",
                "timestamp": 1710000000123,
                "data": response_data,
            }
            self._first_acks[key] = first_ack
            return first_ack
        first_ack = {
            "http_status": 202,
            "code": self.code,
            "timestamp": 1710000000123,
            "data": {"transport_task_id": transport_task_id},
        }
        self._first_acks[key] = first_ack
        return first_ack


class UnavailableRecorder:
    async def record_callback(self, **kwargs: object) -> str:
        raise RuntimeError("database unavailable")


def _body(
    operation: str,
    data: dict[str, object],
    *,
    operation_id: str = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
) -> bytes:
    return json.dumps(
        {"operation_id": operation_id, "operation": operation, "timestamp": 1, "data": data},
        separators=(",", ":"),
    ).encode()


@pytest.mark.asyncio
async def test_valid_position_callback_is_persisted_before_received_ack() -> None:
    recorder = FakeRecorder()
    handler = TransportEventHandler(recorder)
    body = _body(
        "transport.task.member_position_changed@v1",
        {
            "transport_task_id": "transport-1",
            "container_id": "bin-1",
            "milestone": "TARGET_PLACED",
            "final_position": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "A", "slot_id": "1"},
        },
    )

    response = await handler.handle(body)

    assert response.http_status == 202
    assert response.body["code"] == "RECEIVED"
    assert recorder.calls[0]["operation_id"] == "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    assert recorder.calls[0]["message"]["timestamp"] == 1


@pytest.mark.asyncio
async def test_persistence_failure_returns_unavailable_ack() -> None:
    response = await TransportEventHandler(UnavailableRecorder()).handle(
        _body(
            "transport.task.member_position_changed@v1",
            {
                "transport_task_id": "transport-1",
                "container_id": "bin-1",
                "milestone": "SOURCE_PICKED",
            },
        )
    )

    assert response.http_status == 503
    assert response.body["code"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_handler_rejects_oversized_or_non_closed_json_without_persisting() -> None:
    recorder = FakeRecorder()
    handler = TransportEventHandler(recorder)

    oversized = await handler.handle(b"x" * (MAX_TRANSPORT_EVENT_BODY_BYTES + 1))
    unknown = await handler.handle(
        _body(
            "transport.task.member_position_changed@v1",
            {
                "transport_task_id": "transport-1",
                "container_id": "bin-1",
                "milestone": "SOURCE_PICKED",
                "unexpected": True,
            },
        )
    )

    assert oversized.http_status == 413
    assert unknown.http_status == 422
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["rejection_reason_code"] == "INVALID_EVIDENCE"


@pytest.mark.asyncio
async def test_handler_accepts_a_valid_body_at_exactly_256_kib() -> None:
    recorder = FakeRecorder()
    handler = TransportEventHandler(recorder)
    body = _body(
        "transport.task.member_position_changed@v1",
        {
            "transport_task_id": "transport-boundary",
            "container_id": "bin-boundary",
            "milestone": "SOURCE_PICKED",
        },
    )
    exact_body = body + b" " * (MAX_TRANSPORT_EVENT_BODY_BYTES - len(body))

    response = await handler.handle(exact_body)

    assert len(exact_body) == MAX_TRANSPORT_EVENT_BODY_BYTES
    assert response.http_status == 202
    assert recorder.calls[0]["operation_id"] == "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_body", [b"\xff", b"{"])
async def test_handler_rejects_invalid_utf8_or_json(raw_body: bytes) -> None:
    response = await TransportEventHandler(FakeRecorder()).handle(raw_body)
    assert response.http_status == 400


@pytest.mark.asyncio
async def test_handler_rejects_nul_operation_before_association() -> None:
    recorder = FakeRecorder()
    response = await TransportEventHandler(recorder).handle(
        _body(
            "transport.task.member_position_changed@v1\x00",
            {"transport_task_id": "transport-1"},
        )
    )

    assert (response.http_status, response.body) == (400, {})
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_handler_records_nul_payload_as_persistence_safe_rejection() -> None:
    recorder = FakeRecorder()
    response = await TransportEventHandler(recorder).handle(
        _body(
            "transport.task.member_position_changed@v1",
            {
                "transport_task_id": "transport-1",
                "container_id": "bin\x00one",
                "milestone": "SOURCE_PICKED",
            },
        )
    )

    assert response.http_status == 422
    message = recorder.calls[0]["message"]
    assert isinstance(message, dict)
    assert "\x00" not in str(message)
    assert "\\u0000" in message["canonical_message_json"]


@pytest.mark.asyncio
async def test_handler_rejects_nested_duplicate_key_before_association() -> None:
    recorder = FakeRecorder()
    raw_body = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472",'
        b'"operation":"transport.task.member_position_changed@v1","timestamp":1,'
        b'"data":{"transport_task_id":"transport-1","container_id":"bin-1","container_id":"bin-2",'
        b'"milestone":"SOURCE_PICKED"}}'
    )

    response = await TransportEventHandler(recorder).handle(raw_body)

    assert (response.http_status, response.body) == (400, {})
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_handler_rejects_duplicate_operation_id_as_unassociated_bad_request() -> None:
    raw_body = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472",'
        b'"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4473",'
        b'"operation":"transport.task.member_position_changed@v1","timestamp":1,"data":{}}'
    )

    response = await TransportEventHandler(FakeRecorder()).handle(raw_body)

    assert (response.http_status, response.body) == (400, {})


@pytest.mark.asyncio
async def test_handler_rejects_deep_json_without_escaping_recursion_error() -> None:
    recorder = FakeRecorder()
    raw_body = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472",'
        b'"operation":"transport.task.member_position_changed@v1","timestamp":1,"data":'
        + b"[" * 998
        + b"0"
        + b"]" * 998
        + b"}"
    )

    response = await TransportEventHandler(recorder).handle(raw_body)

    assert response.http_status == 422
    assert response.body["operation_id"] == "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_handler_uses_semantic_digest_input_for_deep_invalid_json() -> None:
    recorder = FakeRecorder()
    prefix = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472",'
        b'"operation":"transport.task.member_position_changed@v1","timestamp":1,"data":'
    )
    nested = b"[" * 998 + b"0" + b"]" * 998
    compact = prefix + nested + b"}"
    spaced = prefix.replace(b'"timestamp":1', b'"timestamp" : 1') + nested + b" }"

    first = await TransportEventHandler(recorder).handle(compact)
    second = await TransportEventHandler(recorder).handle(spaced)

    assert (first.http_status, second.http_status) == (422, 200)
    assert recorder.calls[0]["message"] == recorder.calls[1]["message"]


@pytest.mark.asyncio
async def test_handler_distinguishes_unknown_operation_from_invalid_known_evidence() -> None:
    response = await TransportEventHandler(FakeRecorder()).handle(
        _body("transport.task.unknown@v1", {"transport_task_id": "transport-1"})
    )

    assert response.http_status == 422
    assert response.body["data"] == {
        "transport_task_id": "transport-1",
        "reason_code": "UNSUPPORTED_OPERATION",
    }


@pytest.mark.asyncio
async def test_handler_rejects_json_integer_beyond_python_digit_limit() -> None:
    recorder = FakeRecorder()
    raw_body = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472","operation":"transport.task.member_position_changed@v1",'
        b'"timestamp":' + b"1" * 5000 + b',"data":{}}'
    )

    response = await TransportEventHandler(recorder).handle(raw_body)

    assert response.http_status == 400
    assert recorder.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("number", "expected_status", "expected_calls"),
    [
        (b"79228162514264337593543950335.0", 422, 1),
        (b"79228162514264337593543950336.0", 400, 0),
        (b"1e-28", 422, 1),
        (b"1e-29", 400, 0),
        (b"10e-29", 422, 1),
        (b"1e29", 400, 0),
        (b"0e-999999999", 422, 1),
        (b"0e999999999", 422, 1),
    ],
    ids=[
        "max",
        "coefficient-overflow",
        "scale-28",
        "scale-29",
        "reduced-scale-29",
        "positive-overflow",
        "zero-negative-huge-exponent",
        "zero-positive-huge-exponent",
    ],
)
async def test_handler_enforces_exact_system_decimal_float_domain(
    number: bytes,
    expected_status: int,
    expected_calls: int,
) -> None:
    recorder = FakeRecorder()
    raw_body = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472",'
        b'"operation":"transport.task.member_position_changed@v1","timestamp":' + number + b',"data":{}}'
    )

    response = await TransportEventHandler(recorder).handle(raw_body)

    assert response.http_status == expected_status
    assert len(recorder.calls) == expected_calls


@pytest.mark.asyncio
async def test_handler_rejects_unencodable_json_string_without_persisting() -> None:
    recorder = FakeRecorder()
    raw_body = (
        b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472","operation":"transport.task.member_position_changed@v1",'
        b'"timestamp":1,"data":{"transport_task_id":"\\ud800",'
        b'"container_id":"bin-1","milestone":"SOURCE_PICKED"}}'
    )

    response = await TransportEventHandler(recorder).handle(raw_body)

    assert response.http_status == 422
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_handler_does_not_echo_an_unencodable_operation_id() -> None:
    recorder = FakeRecorder()
    raw_body = (
        b'{"operation_id":"\\ud800","operation":"transport.task.member_position_changed@v1","timestamp":1,"data":{}}'
    )

    response = await TransportEventHandler(recorder).handle(raw_body)

    assert (response.http_status, response.body) == (400, {})
    json.dumps(response.body, ensure_ascii=False).encode("utf-8")
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_result_requires_position_xor_unknown_and_failure_code() -> None:
    handler = TransportEventHandler(FakeRecorder())
    invalid = _body(
        "transport.task.resulted@v1",
        {
            "transport_task_id": "transport-1",
            "kind": "BIN_MOVE",
            "results": [{"container_id": "bin-1", "status": "FAILED", "position_unknown": False}],
        },
    )

    response = await handler.handle(invalid)

    assert response.http_status == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_body", [b"{", b"\xff"])
async def test_preassociation_rejections_have_an_empty_body(raw_body: bytes) -> None:
    response = await TransportEventHandler(FakeRecorder()).handle(raw_body)

    assert (response.http_status, response.body) == (400, {})


@pytest.mark.asyncio
async def test_oversized_preassociation_rejection_has_an_empty_body() -> None:
    response = await TransportEventHandler(FakeRecorder()).handle(b"x" * (MAX_TRANSPORT_EVENT_BODY_BYTES + 1))

    assert (response.http_status, response.body) == (413, {})


@pytest.mark.asyncio
async def test_rejected_evidence_is_persisted_and_corrected_event_uses_a_new_id() -> None:
    recorder = FakeRecorder()
    rejected_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    corrected_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4473"
    rejected = await TransportEventHandler(recorder).handle(
        _body(
            "transport.task.member_position_changed@v1",
            {"transport_task_id": "transport-1", "container_id": "bin-1", "milestone": "INVALID"},
            operation_id=rejected_id,
        )
    )
    corrected = await TransportEventHandler(recorder).handle(
        _body(
            "transport.task.member_position_changed@v1",
            {"transport_task_id": "transport-1", "container_id": "bin-1", "milestone": "SOURCE_PICKED"},
            operation_id=corrected_id,
        )
    )

    assert rejected.http_status == 422
    assert rejected.body.get("operation_id") == rejected_id
    assert corrected.http_status == 202
    assert corrected.body.get("operation_id") == corrected_id
    assert len(recorder.calls) == 2
    assert recorder.calls[0]["operation_id"] == rejected_id
    assert recorder.calls[0]["rejection_reason_code"] == "INVALID_EVIDENCE"
    assert recorder.calls[1]["operation_id"] == corrected_id
    assert recorder.calls[1]["rejection_reason_code"] is None


@pytest.mark.asyncio
async def test_duplicate_evidence_ack_reuses_owner_snapshot_across_a_new_handler_session() -> None:
    recorder = FakeRecorder()
    operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    body = _body(
        "transport.task.member_position_changed@v1",
        {"transport_task_id": "transport-1", "container_id": "bin-1", "milestone": "SOURCE_PICKED"},
        operation_id=operation_id,
    )

    first = await TransportEventHandler(recorder).handle(body)
    duplicate = await TransportEventHandler(recorder).handle(body)

    assert (first.http_status, duplicate.http_status) == (202, 200)
    assert first.body["operation_id"] == duplicate.body["operation_id"] == operation_id
    assert first.body["timestamp"] == duplicate.body["timestamp"]
    assert first.body["data"] == duplicate.body["data"] == {"transport_task_id": "transport-1"}
