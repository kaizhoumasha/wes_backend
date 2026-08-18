"""当前 WMS Transport Mock 的公开 HTTP 可行性探针。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts import verify_wms_northbound_feasibility as probe_module
from scripts.verify_wms_northbound_feasibility import MAX_RESPONSE_BYTES, _matches_ack, _request, run_probe
from tests.mock import wms_mock_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def transport_mock_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wms_mock_server.app),
        base_url="http://mock-wms.test",
        timeout=httpx.Timeout(1),
    ) as client:
        reset = await client.post("/debug/reset")
        assert reset.status_code == 200
        yield client


@pytest.mark.asyncio
async def test_probe_verifies_current_transport_contract_over_public_http(
    transport_mock_client: httpx.AsyncClient,
) -> None:
    report = await run_probe(transport_mock_client, request_timeout_seconds=1)

    assert report.passed is True
    assert {case.case_id for case in report.cases} == {
        "service_contract",
        "operation_identity_scoped",
        "rack_move_received",
        "rack_move_duplicate",
        "rack_move_conflict",
        "active_rack_conflict",
        "rack_rotate_received",
        "rack_rotate_same_face_conflict",
        "rack_rotate_unknown_face_unavailable",
        "bin_move_received",
        "bin_exchange_received",
        "closed_payload_rejected",
    }


@pytest.mark.asyncio
async def test_probe_stops_streaming_when_response_exceeds_the_fixed_limit() -> None:
    class CountingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.chunks_read = 0

        async def __aiter__(self):
            for _ in range(100):
                self.chunks_read += 1
                yield b"x" * (64 * 1024)

    stream = CountingStream()

    async def oversized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized), base_url="http://mock-wms.test") as client:
        response = await _request(client, "GET", "/oversized", request_timeout_seconds=1)

    assert response is None
    assert stream.chunks_read <= MAX_RESPONSE_BYTES // (64 * 1024) + 1


@pytest.mark.parametrize(
    ("headers", "timestamp"),
    [
        ({"Content-Type": "text/plain"}, 1786060800000),
        ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, 1786060800000),
        ({"Content-Type": "application/json"}, True),
        ({"Content-Type": "application/json"}, -1),
        ({"Content-Type": "application/json"}, 2**63),
    ],
)
def test_probe_rejects_ack_that_the_production_adapter_treats_as_delivery_unknown(
    headers: dict[str, str], timestamp: object
) -> None:
    operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
    response = httpx.Response(
        202,
        headers={"Content-Type": headers["Content-Type"]},
        json={
            "operation_id": operation_id,
            "code": "RECEIVED",
            "timestamp": timestamp,
            "data": {"transport_task_id": "transport-1"},
        },
    )
    if content_encoding := headers.get("Content-Encoding"):
        response.headers["Content-Encoding"] = content_encoding

    assert not _matches_ack(
        response,
        status_code=202,
        operation_id=operation_id,
        code="RECEIVED",
        transport_task_id="transport-1",
    )


@pytest.mark.asyncio
async def test_probe_can_run_twice_against_the_same_provider_process(
    transport_mock_client: httpx.AsyncClient,
) -> None:
    first = await run_probe(transport_mock_client, request_timeout_seconds=1)
    second = await run_probe(transport_mock_client, request_timeout_seconds=1)

    assert first.passed is True
    assert second.passed is True


def test_feasibility_probe_script_is_directly_executable() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts/verify_wms_northbound_feasibility.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=False,
        capture_output=True,
        cwd=script.parent,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--base-url" in completed.stdout


@pytest.mark.asyncio
async def test_probe_cli_ignores_host_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        probe_module,
        "_parse_args",
        lambda: SimpleNamespace(base_url="http://127.0.0.1:8011", timeout_seconds=1),
    )
    monkeypatch.setattr(probe_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        probe_module,
        "run_probe",
        AsyncMock(return_value=probe_module.FeasibilityReport(cases=())),
    )

    assert await probe_module._main() == 0
    assert captured["trust_env"] is False
