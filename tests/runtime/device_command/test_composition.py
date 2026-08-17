"""DeviceCommand 唯一生产组合根。"""

from __future__ import annotations

import pytest

from src.app.device.composition import build_device_command_runtime, validate_ecs_base_url


class FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    async def send(self, request):
        raise AssertionError(request)

    async def aclose(self) -> None:
        self.closed = True


def test_ecs_base_url_requires_plain_lan_http_origin() -> None:
    assert validate_ecs_base_url("http://192.168.1.20:8080") == "http://192.168.1.20:8080"
    assert validate_ecs_base_url("http://mock_ecs:8010") == "http://mock_ecs:8010"
    for invalid in (
        "https://192.168.1.20",
        "http://" + "user:pass@192.168.1.20",
        "http://example.com",
        "http://192.168.1.20/path",
    ):
        with pytest.raises(ValueError):
            validate_ecs_base_url(invalid)


@pytest.mark.asyncio
async def test_runtime_owns_one_transport_and_closes_it() -> None:
    transport = FakeTransport()
    task_queue_gateway = object()
    runtime = build_device_command_runtime(
        session_factory=object(),  # type: ignore[arg-type]
        base_url="http://192.168.1.20:8080",
        timeout_seconds=3.0,
        transport=transport,
        task_queue_gateway=task_queue_gateway,  # type: ignore[arg-type]
    )

    assert runtime.dispatch_service._adapter is runtime.adapter
    assert runtime.evidence_service is not None
    assert runtime.evidence_service._task_queue is task_queue_gateway
    await runtime.aclose()
    assert transport.closed is True
