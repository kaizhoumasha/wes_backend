"""WMS EFFECT frozen binding 的无循环 lazy 边界。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.sys.external_http_binding import FrozenExternalHttpBinding
    from src.app.sys.services.endpoint_registry import EndpointRegistry


def freeze_wms_effect_binding(
    *,
    profile_identity: str,
    operation_identity: str,
    target_code: str,
    registry: EndpointRegistry | None = None,
) -> FrozenExternalHttpBinding:
    """延迟读取当前部署唯一 active Provider，避免 operation package 初始化环。"""

    from src.app.runtime.system_capabilities.wms.provider_catalog import (
        freeze_wms_effect_binding as freeze_from_catalog,
    )

    return freeze_from_catalog(
        profile_identity=profile_identity,
        operation_identity=operation_identity,
        target_code=target_code,
        registry=registry,
    )


__all__ = ["freeze_wms_effect_binding"]
