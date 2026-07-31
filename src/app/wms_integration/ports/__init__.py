"""wms_integration ports — Protocol 边界与 operation-specific typed contracts。

QUERY 由 operation-specific typed Definition + WmsQueryExecutionPort 执行，
EFFECT 由 operation-specific typed Definition + WmsEffectPreparationPort 准备。
effect status 与 inbound event 保留各自独立的窄边界，不再公开领域聚合 facade。
"""

_EFFECT_STATUS_EXPORTS = frozenset(
    {
        "FrozenWmsEffectStatusBinding",
        "WmsEffectStatus",
        "WmsEffectStatusQueryPort",
        "WmsEffectStatusRequest",
        "WmsEffectStatusSnapshot",
        "build_wms_effect_status_binding",
    }
)

__all__ = [
    "FrozenWmsEffectStatusBinding",
    "WmsEffectStatus",
    "WmsEffectStatusQueryPort",
    "WmsEffectStatusRequest",
    "WmsEffectStatusSnapshot",
    "build_wms_effect_status_binding",
]


def __getattr__(name: str):
    """延迟解析 status port，避免静态 operation registry 初始化时形成导入环。"""

    if name not in _EFFECT_STATUS_EXPORTS:
        raise AttributeError(name)
    from . import effect_status

    return getattr(effect_status, name)
