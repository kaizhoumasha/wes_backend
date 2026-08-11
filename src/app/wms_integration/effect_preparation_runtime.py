"""WMS EFFECT preparation runtime 的部署级 owner。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    wms_fulfillment_domain_projector,
)
from src.app.runtime.system_capabilities.wms.effect_runtime import WmsEffectPreparationRuntime
from src.app.wms_integration.operation_registry import EFFECT_OPERATION_IDENTITIES

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition
    from src.app.runtime.system_capabilities.wms.provider_catalog import WmsProviderCatalog


_active_runtime: WmsEffectPreparationRuntime | None = None
_active_loop: asyncio.AbstractEventLoop | None = None


def freeze_wms_effect_admission_policy(
    admission_enabled: bool,
) -> Callable[[SystemCapabilityDefinition], bool]:
    """冻结仅控制 13 项 WMS EFFECT 的新 claim policy。"""

    enabled = bool(admission_enabled)

    def allow_new_claim(definition: SystemCapabilityDefinition) -> bool:
        identity = f"{definition.capability_key}@{definition.contract_version}"
        return identity not in EFFECT_OPERATION_IDENTITIES or enabled

    return allow_new_claim


def build_wms_effect_preparation_runtime(
    *,
    catalog: WmsProviderCatalog,
    admission_enabled: bool,
) -> WmsEffectPreparationRuntime:
    """从已经校验的部署 catalog 构造事务内 EFFECT preparation runtime。"""

    return WmsEffectPreparationRuntime(
        catalog=catalog,
        domain_projector=wms_fulfillment_domain_projector,
        allow_new_claim=freeze_wms_effect_admission_policy(admission_enabled),
    )


def bind_wms_effect_preparation_runtime(runtime: WmsEffectPreparationRuntime) -> None:
    """发布当前进程/事件循环唯一的 EFFECT preparation runtime。"""

    global _active_loop, _active_runtime
    loop = asyncio.get_running_loop()
    if _active_runtime is not None and (_active_runtime is not runtime or _active_loop is not loop):
        raise RuntimeError("WMS EFFECT preparation runtime is already bound")
    _active_runtime = runtime
    _active_loop = loop


def get_wms_effect_preparation_runtime() -> WmsEffectPreparationRuntime | None:
    """返回当前事件循环已绑定 runtime；未初始化时保持显式 None。"""

    if _active_runtime is None:
        return None
    if _active_loop is not asyncio.get_running_loop():
        raise RuntimeError("WMS EFFECT preparation runtime event loop mismatch")
    return _active_runtime


def unbind_wms_effect_preparation_runtime(runtime: WmsEffectPreparationRuntime) -> None:
    """仅允许 owner 在关闭前撤销自身 runtime。"""

    global _active_loop, _active_runtime
    if _active_runtime is not runtime:
        raise RuntimeError("cannot unbind a different WMS EFFECT preparation runtime")
    if _active_loop is not asyncio.get_running_loop():
        raise RuntimeError("WMS EFFECT preparation runtime event loop mismatch during unbind")
    _active_runtime = None
    _active_loop = None


async def close_wms_effect_preparation_runtime(runtime: WmsEffectPreparationRuntime) -> None:
    """仅撤销本次成功绑定的 owner runtime；不拥有外部资源。"""

    unbind_wms_effect_preparation_runtime(runtime)


async def close_bound_wms_effect_preparation_runtime() -> None:
    """撤销当前 owner loop 的 runtime；它不拥有需要回收的外部资源。"""

    runtime = get_wms_effect_preparation_runtime()
    if runtime is not None:
        unbind_wms_effect_preparation_runtime(runtime)


__all__ = [
    "bind_wms_effect_preparation_runtime",
    "build_wms_effect_preparation_runtime",
    "close_bound_wms_effect_preparation_runtime",
    "close_wms_effect_preparation_runtime",
    "freeze_wms_effect_admission_policy",
    "get_wms_effect_preparation_runtime",
    "unbind_wms_effect_preparation_runtime",
]
