"""wms_integration ports — Protocol 边界与 operation-specific typed contracts。

按主计划 §3.5.1 + §5.1 拆分:

活跃 (当前里程碑):
1. WmsMasterDataPort (物料主数据, 包括 area/warehouse/storage_location/equipment)
2. WmsQueryExecutionPort (19 项 registry-driven typed QUERY)
3. WmsInventoryTransactionPort (库存事务: reserve/release/confirm/transfer)
4. WmsReconciliationQueryPort (对账 drift 查询)

单据和履约不再公开粗粒度 Protocol；Q08–Q13/Q19 与 E07–E16 分别只由
document_operations、fulfillment_operations 的 operation-specific Definition 表达。
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
