"""Runtime Hold 服务的惰性导出入口。"""

from importlib import import_module

_EXPORTS = {
    "RuntimeHoldCreationService": ".runtime_hold_creation_service",
    "runtime_hold_creation_service": ".runtime_hold_creation_service",
    "RuntimeHoldQueryService": ".runtime_hold_query_service",
    "runtime_hold_query_service": ".runtime_hold_query_service",
    "RuntimeHoldReleaseError": ".runtime_hold_release_service",
    "RuntimeHoldReleaseService": ".runtime_hold_release_service",
    "runtime_hold_release_service": ".runtime_hold_release_service",
    "WmsPutawaySyncBarrierEvaluation": ".wms_putaway_sync_barrier_service",
    "WmsPutawaySyncBarrierGroup": ".wms_putaway_sync_barrier_service",
    "WmsPutawaySyncBarrierService": ".wms_putaway_sync_barrier_service",
    "wms_putaway_sync_barrier_service": ".wms_putaway_sync_barrier_service",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if module_name := _EXPORTS.get(name):
        return getattr(import_module(module_name, __name__), name)
    raise AttributeError(name)
