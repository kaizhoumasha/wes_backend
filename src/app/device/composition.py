"""DeviceCommand/ECS 唯一生产组合根。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from src.app.device.ecs_adapter import EcsAdapter
from src.app.device.endpoint import validate_device_endpoint_base_url
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.device.services.device_dispatch_service import DeviceDispatchService
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.app.sys.services.event_stream_service import event_stream_service
from src.core.outbound_http import OutboundHttpTransport, build_outbound_http_transport

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.core.task_queue_gateway import TaskQueueGateway


class _TransportFactory(Protocol):
    def __call__(self, endpoint_base_url: str, timeout_seconds: float) -> OutboundHttpTransport: ...


def _build_ecs_transport(endpoint_base_url: str, timeout_seconds: float) -> OutboundHttpTransport:
    return build_outbound_http_transport(
        system_id="ecs",
        base_url=endpoint_base_url,
        timeout_seconds=timeout_seconds,
    )


class DeviceEndpointAdapterProvider:
    """在当前进程内按 canonical Endpoint 惰性复用 Adapter/transport。"""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport_factory: _TransportFactory = _build_ecs_transport,
        adapter_factory: Callable[[OutboundHttpTransport], EcsAdapter] = EcsAdapter,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._transport_factory = transport_factory
        self._adapter_factory = adapter_factory
        self._adapters: dict[str, EcsAdapter] = {}
        self._transports: dict[str, OutboundHttpTransport] = {}
        self._closed = False

    async def get_adapter(self, endpoint_base_url: str) -> EcsAdapter:
        if self._closed:
            raise RuntimeError("Device Endpoint provider 已关闭")
        endpoint = validate_device_endpoint_base_url(endpoint_base_url)
        existing = self._adapters.get(endpoint)
        if existing is not None:
            return existing

        transport = self._transport_factory(endpoint, self._timeout_seconds)
        try:
            adapter = self._adapter_factory(transport)
        except BaseException:
            await transport.aclose()
            raise
        self._transports[endpoint] = transport
        self._adapters[endpoint] = adapter
        return adapter

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        transports = tuple(self._transports.values())
        self._transports.clear()
        self._adapters.clear()
        first_error: BaseException | None = None
        for transport in transports:
            try:
                await transport.aclose()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


@dataclass(slots=True)
class DeviceCommandRuntime:
    provider: DeviceEndpointAdapterProvider
    command_service: DeviceCommandService
    dispatch_service: DeviceDispatchService
    evidence_service: DeviceEvidenceService

    async def aclose(self) -> None:
        await self.provider.aclose()


@dataclass(frozen=True, slots=True)
class DeviceCommandRuntimeConfig:
    timeout_seconds: float
    queue: str


def resolve_device_command_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> DeviceCommandRuntimeConfig:
    """从设备 runtime 专属环境变量构造不可变、fail-closed 配置。"""

    values = os.environ if environ is None else environ
    try:
        connect_timeout = float(values["ECS_CONNECT_TIMEOUT_SECONDS"])
        read_timeout = float(values["ECS_READ_TIMEOUT_SECONDS"])
        queue = values["DEVICE_COMMAND_QUEUE"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("ECS runtime 配置缺失或格式无效") from error
    if connect_timeout <= 0 or read_timeout <= 0:
        raise ValueError("ECS connect/read timeout 必须大于 0")
    if queue != "device-command":
        raise ValueError("DEVICE_COMMAND_QUEUE 必须固定为 device-command")
    return DeviceCommandRuntimeConfig(
        timeout_seconds=connect_timeout + read_timeout,
        queue=queue,
    )


def build_device_command_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    timeout_seconds: float,
    task_queue_gateway: TaskQueueGateway,
    transport_factory: _TransportFactory = _build_ecs_transport,
    adapter_factory: Callable[[OutboundHttpTransport], EcsAdapter] = EcsAdapter,
) -> DeviceCommandRuntime:
    provider = DeviceEndpointAdapterProvider(
        timeout_seconds=timeout_seconds,
        transport_factory=transport_factory,
        adapter_factory=adapter_factory,
    )
    return DeviceCommandRuntime(
        provider=provider,
        command_service=DeviceCommandService(session_factory=session_factory, adapter_provider=provider),
        dispatch_service=DeviceDispatchService(
            session_factory=session_factory,
            adapter_provider=provider,
        ),
        evidence_service=DeviceEvidenceService(
            session_factory=session_factory,
            task_queue_gateway=task_queue_gateway,
            event_publisher=event_stream_service,
        ),
    )


__all__ = [
    "DeviceCommandRuntime",
    "DeviceCommandRuntimeConfig",
    "DeviceEndpointAdapterProvider",
    "build_device_command_runtime",
    "resolve_device_command_runtime_config",
]
