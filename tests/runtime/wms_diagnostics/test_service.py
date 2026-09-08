"""诊断故障不能占用完成发布份额或吞掉原取消。"""

import asyncio
import importlib
import time
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ValidationError

from src.app.wms_diagnostics.config import DiagnosticsConfig
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed
from src.app.wms_diagnostics.service import WmsDiagnosticsService


def test_expired_comparison_budget_never_reports_contract_pass() -> None:
    observation = WmsCallObservation(direction="WES_TO_WMS")
    observation.request_body = observation.response_body = b"{}"
    observation.request_validated = observation.response_validated = True
    service = WmsDiagnosticsService(AsyncMock(), AsyncMock(), DiagnosticsConfig())
    event = service._build(observation, deadline=time.monotonic() - 1)
    assert event.incomplete
    assert event.contract_status == "NOT_VALIDATED"


def test_finished_transport_without_http_response_is_not_empty_response() -> None:
    observation = WmsCallObservation(direction="WES_TO_WMS")
    observation.elapsed_ms = 10
    observation.response_body = b""
    service = WmsDiagnosticsService(AsyncMock(), AsyncMock(), DiagnosticsConfig())
    event = service._build(observation, deadline=time.monotonic() + 1)
    assert event.response.state == "NO_RESPONSE"
    assert event.response.body is None


async def test_wire_retains_protocol_headers_but_never_credentials() -> None:
    repository = AsyncMock()
    repository.append.return_value = "2000000000000-1"
    service = WmsDiagnosticsService(repository, AsyncMock(), DiagnosticsConfig())
    observation = WmsCallObservation(direction="WES_TO_WMS")
    observation.request_headers = (("Content-Type", "application/json"), ("Authorization", "private"))
    observation.response_headers = (("Content-Encoding", "gzip"), ("Set-Cookie", "private"))
    assert await service.finish(observation)
    encoded = repository.append.call_args.args[0]
    assert "application/json" in encoded and "gzip" in encoded
    assert "private" not in encoded and "Authorization" not in encoded and "Set-Cookie" not in encoded


async def test_history_timeout_still_publishes_unsaved_completion() -> None:
    repository = AsyncMock()

    async def blocked(encoded):
        await asyncio.Event().wait()

    repository.append.side_effect = blocked
    publisher = AsyncMock()
    publisher.publish_to.return_value = True
    service = WmsDiagnosticsService(repository, publisher, DiagnosticsConfig(budget_ms=100))
    observation = WmsCallObservation(direction="WES_TO_WMS", operation="sample@v1", operation_id="op-1")
    observation.status_code = 200
    observation.result = "ACCEPTED"
    assert await service.finish(observation) is False
    event = publisher.publish_to.call_args.args
    assert event[1] == "wms_exchange.completed"
    assert event[2]["exchange_id"] is None
    assert event[2]["result"] == "ACCEPTED"
    assert event[2]["incomplete"] is True


async def test_started_timeout_preserves_completion_publish_budget() -> None:
    publisher = AsyncMock()

    async def publish(_channel, kind, _payload):
        if kind == "wms_exchange.started":
            await asyncio.Event().wait()
        return True

    publisher.publish_to.side_effect = publish
    repository = AsyncMock()
    repository.append.return_value = "2000000000000-1"
    service = WmsDiagnosticsService(repository, publisher, DiagnosticsConfig())
    observation = await service.start(direction="WES_TO_WMS", payload={"count": 1})
    assert observation is not None and observation.incomplete
    assert await service.finish(observation)
    assert publisher.publish_to.call_args.args[1] == "wms_exchange.completed"


@pytest.mark.parametrize("stage", ["schema", "compare", "redact", "serialize"])
async def test_processing_failure_only_publishes_safe_minimal_completion(monkeypatch, stage) -> None:
    class Request(BaseModel):
        count: int

    def fail(*args, **kwargs):
        raise RuntimeError("private body must not leak")

    observation = WmsCallObservation(direction="WES_TO_WMS", operation_id="op-1")
    observation.request_body = b'{"count":1}'
    observation.request_contract = Request
    observation.result = "REJECTED"
    service_module = importlib.import_module("src.app.wms_diagnostics.service")
    repository = AsyncMock()
    publisher = AsyncMock()
    service = WmsDiagnosticsService(repository, publisher, DiagnosticsConfig())
    if stage == "schema":
        monkeypatch.setattr(Request, "model_json_schema", fail)
    elif stage == "compare":
        monkeypatch.setattr(service_module, "compare_fields", fail)
    elif stage == "redact":
        monkeypatch.setattr(service_module, "preview_json", fail)
    else:
        bounded_json = service._bounded_json

        def encode(event, limit):
            if event.request.body is not None:
                fail()
            return bounded_json(event, limit)

        monkeypatch.setattr(service, "_bounded_json", encode)
    assert not await service.finish(observation)
    repository.append.assert_not_awaited()
    event = publisher.publish_to.call_args.args[2]
    assert event["operation_id"] == "op-1" and event["result"] == "REJECTED"
    assert event["incomplete"] and event["exchange_id"] is None
    assert "private body" not in str(event)


async def test_completion_saves_once_and_does_not_duplicate_on_second_finish() -> None:
    repository = AsyncMock()
    repository.append.return_value = "2000000000000-1"
    publisher = AsyncMock()
    service = WmsDiagnosticsService(repository, publisher, DiagnosticsConfig())
    observation = WmsCallObservation(direction="WMS_TO_WES")
    assert await service.finish(observation) is True
    assert await service.finish(observation) is False
    repository.append.assert_awaited_once()
    publisher.publish_to.assert_awaited_once()


async def test_cancelled_diagnostics_propagates_without_completion_background_task() -> None:
    repository = AsyncMock()
    repository.append.side_effect = asyncio.CancelledError()
    publisher = AsyncMock()
    service = WmsDiagnosticsService(repository, publisher, DiagnosticsConfig())
    with pytest.raises(asyncio.CancelledError):
        await service.finish(WmsCallObservation(direction="WES_TO_WMS"))
    publisher.publish_to.assert_not_awaited()


async def test_publish_failure_preserves_saved_history_and_body_size_limit() -> None:
    repository = AsyncMock()
    repository.append.return_value = "2000000000000-1"
    publisher = AsyncMock()
    publisher.publish_to.side_effect = ConnectionError("offline")
    service = WmsDiagnosticsService(repository, publisher, DiagnosticsConfig(max_record_bytes=4096))
    observation = WmsCallObservation(direction="WES_TO_WMS")
    observation.request_body = b'{"data":"' + b"x" * 20000 + b'"}'
    assert await service.finish(observation) is True
    assert len(repository.append.call_args.args[0].encode()) <= 4096


async def test_failed_original_validation_is_rendered_using_its_contract() -> None:
    class Request(BaseModel):
        count: int

    observation = WmsCallObservation(direction="WES_TO_WMS")
    observation.request_body = b'{"count":"bad"}'
    observation.request_source = "WIRE"
    with pytest.raises(ValidationError):
        validate_observed(Request, {"count": "bad"}, observation=observation, side="request")
    repository = AsyncMock()
    repository.append.return_value = "2000000000000-1"
    publisher = AsyncMock()
    await WmsDiagnosticsService(repository, publisher, DiagnosticsConfig()).finish(observation)
    record = publisher.publish_to.call_args.args[2]
    field = next(item for item in record["comparisons"] if item["path"] == "$.count")
    assert field["verdict"] == "ERROR"
    assert "integer" in field["expected_rule"]
    assert field["actual_value"] == "bad"


async def test_stream_filters_valid_events_and_never_emits_replay_id() -> None:
    from src.app.wms_diagnostics.contracts import ExchangeObservation, ExchangeQuery

    event = ExchangeObservation(
        attempt_id="attempt-1", observed_at="2026-09-07T12:00:00Z", direction="WES_TO_WMS", operation="sample@v1"
    )

    class Publisher:
        async def subscribe(self, channel, *, timeout_seconds):
            yield None
            yield {"type": "unknown", "payload": {}}
            yield {"type": "wms_exchange.completed", "payload": event.model_dump(mode="json")}

    service = WmsDiagnosticsService(AsyncMock(), Publisher(), DiagnosticsConfig())
    frames = [frame async for frame in service.stream_events(ExchangeQuery(operation="sample@v1"))]
    assert frames[0] == ": heartbeat\n\n"
    assert "event: wms_exchange.completed\n" in frames[1]
    assert "\nid:" not in frames[1]
    frames = [frame async for frame in service.stream_events(ExchangeQuery(operation="different@v1"))]
    assert frames == [": heartbeat\n\n"]
