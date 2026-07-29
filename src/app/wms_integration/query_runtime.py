"""WES 进程 `wms-data` lane 的 19 项 QUERY 运行时。"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from src.app.runtime.system_capabilities.wms.provider_catalog import freeze_wms_query_binding
from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.query_executor import WmsRegistryQueryExecutor
from src.app.wms_integration.query_projection import WmsQueryRequestProjection, project_wms_query_request

if TYPE_CHECKING:
    import httpx
    from pydantic import BaseModel

    from src.app.runtime.system_capabilities.wms.provider_catalog import WmsProviderCatalog
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile
    from src.app.wms_integration.ports.query_outcome import WmsQueryOutcome
    from src.app.wms_integration.provider_startup import WmsProviderStartupConfiguration
    from src.app.wms_integration.query_executor import (
        WmsQueryCredentialProvider,
        WmsRegistryQueryEvidenceWriter,
    )


class WmsDataLaneQueryRuntime:
    """为同一进程/事件循环/data lane 的全部 QUERY 共享一个长期 client。"""

    def __init__(
        self,
        *,
        compiled_profile: CompiledWmsProviderProfile,
        catalog: WmsProviderCatalog,
        client: httpx.AsyncClient,
        credential_provider: WmsQueryCredentialProvider,
        evidence_writer: WmsRegistryQueryEvidenceWriter,
    ) -> None:
        if catalog.compiled_profile is not compiled_profile:
            raise ValueError("QUERY runtime requires one compiled profile snapshot")
        executors = {
            operation.identity: WmsRegistryQueryExecutor(
                operation=operation,
                endpoint=compiled_profile.operations[operation.identity],
                frozen_binding=freeze_wms_query_binding(
                    catalog=catalog,
                    operation_identity=operation.identity,
                ),
                client=client,
                credential_provider=credential_provider,
                evidence_writer=evidence_writer,
            )
            for operation in QUERY_OPERATIONS
        }
        request_identities = {operation.request_model: operation.identity for operation in QUERY_OPERATIONS}
        if len(executors) != 19 or len(request_identities) != 19:
            raise RuntimeError("wms-data QUERY runtime requires 19 unique identities and request models")
        self._client = client
        self._executors = MappingProxyType(executors)
        self._request_identities = MappingProxyType(request_identities)
        self._operations_by_request = MappingProxyType(
            {operation.request_model: operation for operation in QUERY_OPERATIONS}
        )
        self._endpoints = compiled_profile.operations

    @property
    def operation_identities(self) -> tuple[str, ...]:
        return tuple(self._executors)

    def executor(self, operation_identity: str) -> WmsRegistryQueryExecutor:
        try:
            return self._executors[operation_identity]
        except KeyError as exc:
            raise LookupError("unknown WMS QUERY operation identity") from exc

    async def execute(self, request: BaseModel) -> WmsQueryOutcome[Any]:
        try:
            operation_identity = self._request_identities[type(request)]
        except KeyError as exc:
            raise TypeError("unregistered WMS QUERY request model") from exc
        return await self._executors[operation_identity].execute(request)

    def project(self, request: BaseModel) -> WmsQueryRequestProjection:
        try:
            operation = self._operations_by_request[type(request)]
        except KeyError as exc:
            raise TypeError("unregistered WMS QUERY request model") from exc
        return project_wms_query_request(
            operation=operation,
            endpoint=self._endpoints[operation.identity],
            request=request,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


_active_runtime: WmsDataLaneQueryRuntime | None = None
_active_loop: asyncio.AbstractEventLoop | None = None


def bind_wms_data_lane_query_runtime(runtime: WmsDataLaneQueryRuntime) -> None:
    """发布当前进程/事件循环唯一的 data lane QUERY runtime。"""

    global _active_loop, _active_runtime
    loop = asyncio.get_running_loop()
    if _active_runtime is not None and (_active_runtime is not runtime or _active_loop is not loop):
        raise RuntimeError("wms-data QUERY runtime is already bound")
    _active_runtime = runtime
    _active_loop = loop


def get_wms_data_lane_query_runtime() -> WmsDataLaneQueryRuntime | None:
    """返回当前事件循环已绑定的 runtime；未初始化时保持显式 None。"""

    if _active_runtime is None:
        return None
    if _active_loop is not asyncio.get_running_loop():
        raise RuntimeError("wms-data QUERY runtime event loop mismatch")
    return _active_runtime


def unbind_wms_data_lane_query_runtime(runtime: WmsDataLaneQueryRuntime) -> None:
    """仅允许 owner 在关闭前撤销自身 runtime。"""

    global _active_loop, _active_runtime
    if _active_runtime is not runtime:
        raise RuntimeError("cannot unbind a different wms-data QUERY runtime")
    _active_runtime = None
    _active_loop = None


def build_wms_data_lane_query_runtime(
    startup: WmsProviderStartupConfiguration,
    *,
    settings_source: Any,
) -> WmsDataLaneQueryRuntime:
    """在 owner loop 内装配一个长期 client 与现有 credential/evidence 边界。"""

    import httpx

    from src.app.sys.external_http_credentials import build_environment_external_http_credential_provider
    from src.app.wms_integration.query_evidence import WmsRegistryCallEvidenceWriter
    from src.app.wms_integration.services.circuit_breaker_service import wms_circuit_breaker_service
    from src.app.wms_integration.services.evidence_service import wms_call_evidence_service
    from src.database.db import get_db_context

    return WmsDataLaneQueryRuntime(
        compiled_profile=startup.compiled_profile,
        catalog=startup.catalog,
        client=httpx.AsyncClient(
            # operation 的 asyncio total deadline 更短；client timeout 仅作为底层 socket 兜底。
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        ),
        credential_provider=build_environment_external_http_credential_provider(settings_source=settings_source),
        evidence_writer=WmsRegistryCallEvidenceWriter(
            session_factory=get_db_context,
            evidence_service=wms_call_evidence_service,
            breaker_service=wms_circuit_breaker_service,
        ),
    )


async def close_bound_wms_data_lane_query_runtime() -> None:
    """关闭并撤销当前 owner loop 的唯一 runtime。"""

    runtime = get_wms_data_lane_query_runtime()
    if runtime is None:
        return
    unbind_wms_data_lane_query_runtime(runtime)
    await runtime.aclose()


__all__ = [
    "WmsDataLaneQueryRuntime",
    "bind_wms_data_lane_query_runtime",
    "build_wms_data_lane_query_runtime",
    "close_bound_wms_data_lane_query_runtime",
    "get_wms_data_lane_query_runtime",
    "unbind_wms_data_lane_query_runtime",
]
