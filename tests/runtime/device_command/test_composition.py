"""DeviceCommand Endpoint provider 的唯一生产组合根。"""

from __future__ import annotations

import pytest

from src.app.device.composition import (
    DeviceEndpointAdapterProvider,
    build_device_command_runtime,
    resolve_device_command_runtime_config,
)


class FakeTransport:
    def __init__(self, endpoint_base_url: str) -> None:
        self.endpoint_base_url = endpoint_base_url
        self.close_count = 0

    async def send(self, request):
        raise AssertionError(request)

    async def aclose(self) -> None:
        self.close_count += 1


class FakeTransportFactory:
    def __init__(self) -> None:
        self.created: list[FakeTransport] = []

    def __call__(self, endpoint_base_url: str, timeout_seconds: float) -> FakeTransport:
        assert timeout_seconds == 3.0
        transport = FakeTransport(endpoint_base_url)
        self.created.append(transport)
        return transport


@pytest.mark.asyncio
async def test_provider_reuses_canonical_endpoint_and_isolates_different_ports() -> None:
    transports = FakeTransportFactory()
    provider = DeviceEndpointAdapterProvider(timeout_seconds=3.0, transport_factory=transports)

    first = await provider.get_adapter("HTTP://ECS-A:80/")
    same = await provider.get_adapter("http://ecs-a")
    different_port = await provider.get_adapter("http://ecs-a:8080")

    assert first is same
    assert first is not different_port
    assert [transport.endpoint_base_url for transport in transports.created] == [
        "http://ecs-a",
        "http://ecs-a:8080",
    ]


@pytest.mark.asyncio
async def test_provider_closes_partial_initialization_and_shutdown_is_idempotent() -> None:
    transports = FakeTransportFactory()

    def reject_adapter(_transport: FakeTransport):
        raise RuntimeError("adapter init failed")

    provider = DeviceEndpointAdapterProvider(
        timeout_seconds=3.0,
        transport_factory=transports,
        adapter_factory=reject_adapter,
    )
    with pytest.raises(RuntimeError, match="adapter init failed"):
        await provider.get_adapter("http://ecs-a:8080")
    assert transports.created[0].close_count == 1

    healthy_transports = FakeTransportFactory()
    healthy = DeviceEndpointAdapterProvider(timeout_seconds=3.0, transport_factory=healthy_transports)
    await healthy.get_adapter("http://ecs-a:8080")
    await healthy.get_adapter("http://ecs-b:8080")
    await healthy.aclose()
    await healthy.aclose()
    assert [transport.close_count for transport in healthy_transports.created] == [1, 1]


def test_runtime_config_uses_only_timeout_and_fixed_queue() -> None:
    config = resolve_device_command_runtime_config(
        {
            "ECS_CONNECT_TIMEOUT_SECONDS": "1",
            "ECS_READ_TIMEOUT_SECONDS": "2",
            "DEVICE_COMMAND_QUEUE": "device-command",
        }
    )

    assert config.timeout_seconds == 3.0
    assert config.queue == "device-command"
    assert not hasattr(config, "base_url")


@pytest.mark.asyncio
async def test_runtime_owns_provider_pool_and_closes_all_transports() -> None:
    transports = FakeTransportFactory()
    task_queue_gateway = object()
    runtime = build_device_command_runtime(
        session_factory=object(),  # type: ignore[arg-type]
        timeout_seconds=3.0,
        transport_factory=transports,
        task_queue_gateway=task_queue_gateway,  # type: ignore[arg-type]
    )

    adapter = await runtime.provider.get_adapter("http://ecs-a:8080")
    assert await runtime.provider.get_adapter("http://ecs-a:8080") is adapter
    assert runtime.dispatch_service._adapter_provider is runtime.provider
    assert runtime.evidence_service._task_queue is task_queue_gateway
    await runtime.aclose()
    assert transports.created[0].close_count == 1
