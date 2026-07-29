"""WMS Provider profile 的进程启动装配。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.app.wms_integration.endpoint_compiler import (
    CompiledWmsProviderProfile,
    compile_wms_provider_profile,
)
from src.app.wms_integration.provider_profile import load_wms_provider_profile
from src.app.wms_integration.provider_readiness import (
    WmsProviderProcessRole,
    WmsProviderReadiness,
    build_wms_provider_readiness,
)


@dataclass(frozen=True, slots=True)
class WmsProviderStartupConfiguration:
    """同一次 profile 编译派生的 WES 与 fulfillment 启动快照。"""

    compiled_profile: CompiledWmsProviderProfile
    wes_readiness: WmsProviderReadiness
    fulfillment_readiness: WmsProviderReadiness


def assemble_wms_provider_startup(settings_source: Any) -> WmsProviderStartupConfiguration:
    """从唯一 Settings 文件入口构建冻结 profile 与两条 lane readiness。"""

    configured_path = getattr(settings_source, "WMS_PROVIDER_PROFILE_FILE", None)
    if configured_path is None:
        raise ValueError("WMS_PROVIDER_PROFILE_FILE must be configured")
    profile_path = Path(configured_path)
    if not profile_path.is_absolute():
        raise ValueError("WMS_PROVIDER_PROFILE_FILE must be an absolute path")
    compiled_profile = compile_wms_provider_profile(load_wms_provider_profile(profile_path))
    return WmsProviderStartupConfiguration(
        compiled_profile=compiled_profile,
        wes_readiness=build_wms_provider_readiness(
            compiled_profile,
            process_role=WmsProviderProcessRole.WES,
        ),
        fulfillment_readiness=build_wms_provider_readiness(
            compiled_profile,
            process_role=WmsProviderProcessRole.FULFILLMENT,
        ),
    )


__all__ = [
    "WmsProviderStartupConfiguration",
    "assemble_wms_provider_startup",
]
