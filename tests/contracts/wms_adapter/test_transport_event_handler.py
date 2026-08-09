from __future__ import annotations

import json

import pytest

from src.app.wms_adapter.transport_event_handler import MAX_TRANSPORT_EVENT_BODY_BYTES, TransportEventHandler


class FakeRecorder:
    def __init__(self, code: str = "RECEIVED") -> None:
        self.code = code
        self.calls: list[dict[str, object]] = []

    async def record_evidence(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.code


def _body(operation: str, data: dict[str, object]) -> bytes:
    return json.dumps(
        {"request_id": "callback-1", "operation": operation, "timestamp": 1, "data": data},
        separators=(",", ":"),
    ).encode()


@pytest.mark.asyncio
async def test_valid_position_callback_is_persisted_before_received_ack() -> None:
    recorder = FakeRecorder()
    handler = TransportEventHandler(recorder)
    body = _body(
        "transport.task.member_position_changed@v1",
        {
            "event_id": "event-1",
            "transport_task_id": "transport-1",
            "bin_id": "bin-1",
            "milestone": "TARGET_PLACED",
            "final_position": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "slot_id": "1"},
        },
    )

    response = await handler.handle(body)

    assert response.http_status == 202
    assert response.body["code"] == "RECEIVED"
    assert recorder.calls[0]["event_id"] == "event-1"


@pytest.mark.asyncio
async def test_handler_rejects_oversized_or_non_closed_json_without_persisting() -> None:
    recorder = FakeRecorder()
    handler = TransportEventHandler(recorder)

    oversized = await handler.handle(b"x" * (MAX_TRANSPORT_EVENT_BODY_BYTES + 1))
    unknown = await handler.handle(
        _body(
            "transport.task.member_position_changed@v1",
            {
                "event_id": "event-1",
                "transport_task_id": "transport-1",
                "bin_id": "bin-1",
                "milestone": "SOURCE_PICKED",
                "unexpected": True,
            },
        )
    )

    assert oversized.http_status == 413
    assert unknown.http_status == 422
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_handler_accepts_a_valid_body_at_exactly_256_kib() -> None:
    recorder = FakeRecorder()
    handler = TransportEventHandler(recorder)
    body = _body(
        "transport.task.member_position_changed@v1",
        {
            "event_id": "event-boundary",
            "transport_task_id": "transport-boundary",
            "bin_id": "bin-boundary",
            "milestone": "SOURCE_PICKED",
        },
    )
    exact_body = body + b" " * (MAX_TRANSPORT_EVENT_BODY_BYTES - len(body))

    response = await handler.handle(exact_body)

    assert len(exact_body) == MAX_TRANSPORT_EVENT_BODY_BYTES
    assert response.http_status == 202
    assert recorder.calls[0]["event_id"] == "event-boundary"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_body", [b"\xff", b"{"])
async def test_handler_rejects_invalid_utf8_or_json(raw_body: bytes) -> None:
    response = await TransportEventHandler(FakeRecorder()).handle(raw_body)
    assert response.http_status == 400


@pytest.mark.asyncio
async def test_result_requires_position_xor_unknown_and_failure_code() -> None:
    handler = TransportEventHandler(FakeRecorder())
    invalid = _body(
        "transport.task.resulted@v1",
        {
            "event_id": "event-result",
            "transport_task_id": "transport-1",
            "kind": "BIN_MOVE",
            "results": [{"object_id": "bin-1", "status": "FAILED", "position_unknown": False}],
        },
    )

    response = await handler.handle(invalid)

    assert response.http_status == 422
