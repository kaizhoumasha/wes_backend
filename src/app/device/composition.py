"""DeviceCommand/ECS 唯一生产组合根。"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from src.app.device.ecs_adapter import EcsAdapter
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.device.services.device_dispatch_service import DeviceDispatchService
from src.app.device.services.device_evidence_service import DeviceEvidenceService
from src.core.outbound_http import OutboundHttpTransport, build_outbound_http_transport

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.core.task_queue_gateway import TaskQueueGateway


@dataclass(slots=True)
class DeviceCommandRuntime:
    transport: OutboundHttpTransport
    adapter: EcsAdapter
    command_service: DeviceCommandService
    dispatch_service: DeviceDispatchService
    evidence_service: DeviceEvidenceService

    async def aclose(self) -> None:
        await self.transport.aclose()


@dataclass(frozen=True, slots=True)
class DeviceCommandRuntimeConfig:
    base_url: str
    timeout_seconds: float
    queue: str


def resolve_device_command_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> DeviceCommandRuntimeConfig:
    """从设备 runtime 专属环境变量构造不可变、fail-closed 配置。"""

    values = os.environ if environ is None else environ
    try:
        base_url = validate_ecs_base_url(values["ECS_BASE_URL"])
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
        base_url=base_url,
        timeout_seconds=connect_timeout + read_timeout,
        queue=queue,
    )


def validate_ecs_base_url(value: str) -> str:
    """只允许纯局域网 HTTP origin，不接受路径、凭据或公网主机名。"""

    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ECS_BASE_URL 必须是无凭据、路径、query 和 fragment 的局域网 http origin")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        # Compose/Kubernetes 内部服务名是单段主机名；带点公网域名不属于局域网配置。
        if "." in parsed.hostname:
            raise ValueError("ECS_BASE_URL 主机必须是局域网 IP 或内部单段服务名") from None
    else:
        if not address.is_private and not address.is_loopback:
            raise ValueError("ECS_BASE_URL 必须指向局域网地址")
    return value.rstrip("/")


def build_device_command_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    base_url: str,
    timeout_seconds: float,
    task_queue_gateway: TaskQueueGateway,
    transport: OutboundHttpTransport | None = None,
) -> DeviceCommandRuntime:
    base_url = validate_ecs_base_url(base_url)
    owned_transport = transport or build_outbound_http_transport(
        system_id="ecs",
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    adapter = EcsAdapter(owned_transport)
    return DeviceCommandRuntime(
        transport=owned_transport,
        adapter=adapter,
        command_service=DeviceCommandService(session_factory=session_factory),
        dispatch_service=DeviceDispatchService(session_factory=session_factory, adapter=adapter),
        evidence_service=DeviceEvidenceService(
            session_factory=session_factory,
            task_queue_gateway=task_queue_gateway,
        ),
    )


__all__ = [
    "DeviceCommandRuntime",
    "DeviceCommandRuntimeConfig",
    "build_device_command_runtime",
    "resolve_device_command_runtime_config",
    "validate_ecs_base_url",
]
