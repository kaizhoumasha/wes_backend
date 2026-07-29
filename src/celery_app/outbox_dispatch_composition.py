"""Celery task root 拥有的 SystemOutbox 静态 claim scope。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from src.app.sys.services.outbox_engine import DispatchResult, SystemOutboxEngine
from src.app.wms_integration.operation_contract import WmsExecutionLane
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.app.wms_integration.provider_readiness import WmsProviderProcessRole

if TYPE_CHECKING:
    from collections.abc import Mapping


class OutboxClaimScopeName(StrEnum):
    """内部 dispatcher scope；公开 lane 名称由任务 target 单独表达。"""

    SYSTEM = "SYSTEM"
    WMS_DATA = "WMS_DATA"
    WMS_DISPATCH = "WMS_DISPATCH"


@dataclass(frozen=True, slots=True)
class OutboxClaimScope:
    """只在 task composition root 构造的 operation identity include/exclude 集合。"""

    included_operation_identities: frozenset[str] | None
    excluded_operation_identities: frozenset[str]


_scoped_engine_cache: dict[OutboxClaimScopeName, SystemOutboxEngine] = {}
_scoped_engine_owner_pid: int | None = None
_scoped_engine_owner_loop: asyncio.AbstractEventLoop | None = None


def build_outbox_claim_scopes() -> Mapping[OutboxClaimScopeName, OutboxClaimScope]:
    """从唯一 registry 派生三组两两互斥 scope，不把 lane 写入数据库。"""

    all_wms_effects = frozenset(operation.identity for operation in EFFECT_OPERATIONS)
    data_effects = frozenset(
        operation.identity for operation in EFFECT_OPERATIONS if operation.execution_lane is WmsExecutionLane.WMS_DATA
    )
    fulfillment_effects = all_wms_effects - data_effects
    return MappingProxyType(
        {
            OutboxClaimScopeName.SYSTEM: OutboxClaimScope(
                included_operation_identities=None,
                excluded_operation_identities=all_wms_effects,
            ),
            OutboxClaimScopeName.WMS_DATA: OutboxClaimScope(
                included_operation_identities=data_effects,
                excluded_operation_identities=frozenset(),
            ),
            OutboxClaimScopeName.WMS_DISPATCH: OutboxClaimScope(
                included_operation_identities=fulfillment_effects,
                excluded_operation_identities=frozenset(),
            ),
        }
    )


async def _dispatch_no_workline_outbox(_db: object, _limit: int) -> DispatchResult:
    """WMS lane 不得抢占 Workline/Rack 等非 WMS outbox。"""

    return {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}


def build_scoped_outbox_engine(scope_name: OutboxClaimScopeName) -> SystemOutboxEngine:
    """为 worker child/owner loop 缓存共享 engine，保留公平调度 cursor。"""

    global _scoped_engine_owner_loop, _scoped_engine_owner_pid
    current_pid = os.getpid()
    current_loop = asyncio.get_running_loop()
    if _scoped_engine_owner_pid is None:
        _scoped_engine_owner_pid = current_pid
        _scoped_engine_owner_loop = current_loop
    elif _scoped_engine_owner_pid != current_pid:
        raise RuntimeError("Outbox scoped engine cache was inherited across a process fork")
    elif _scoped_engine_owner_loop is not current_loop:
        raise RuntimeError("Outbox scoped engine cache event loop mismatch")
    cached = _scoped_engine_cache.get(scope_name)
    if cached is not None:
        return cached

    scope = build_outbox_claim_scopes()[scope_name]
    common = {
        "operation_identities": (
            tuple(sorted(scope.included_operation_identities))
            if scope.included_operation_identities is not None
            else None
        ),
        "exclude_operation_identities": tuple(sorted(scope.excluded_operation_identities)),
    }
    if scope_name is OutboxClaimScopeName.SYSTEM:
        engine = SystemOutboxEngine(**common)
        _scoped_engine_cache[scope_name] = engine
        return engine

    from src.app.wms_integration.effect_lane_runtime import get_wms_effect_lane_runtime

    runtime = get_wms_effect_lane_runtime()
    if runtime is None:
        raise RuntimeError("WMS EFFECT lane runtime is not initialized")
    expected_role = (
        WmsProviderProcessRole.WES
        if scope_name is OutboxClaimScopeName.WMS_DATA
        else WmsProviderProcessRole.FULFILLMENT
    )
    if runtime.process_role is not expected_role:
        raise RuntimeError("WMS EFFECT dispatcher task does not match the worker process role")
    if scope.included_operation_identities != runtime.operation_identities:
        raise RuntimeError("WMS EFFECT dispatcher claim scope/readiness mismatch")
    engine = SystemOutboxEngine(
        **common,
        external_http_sender=runtime.send,
        workline_domain_dispatcher=_dispatch_no_workline_outbox,
    )
    _scoped_engine_cache[scope_name] = engine
    return engine


def clear_scoped_outbox_engine_cache() -> None:
    """仅允许 owner child/loop 在 worker shutdown 时清空进程内 cursor。"""

    global _scoped_engine_owner_loop, _scoped_engine_owner_pid
    if _scoped_engine_owner_pid is None:
        return
    if _scoped_engine_owner_pid != os.getpid():
        raise RuntimeError("cannot clear a fork-inherited Outbox scoped engine cache")
    if _scoped_engine_owner_loop is not asyncio.get_running_loop():
        raise RuntimeError("cannot clear Outbox scoped engine cache from a different event loop")
    _scoped_engine_cache.clear()
    _scoped_engine_owner_pid = None
    _scoped_engine_owner_loop = None


__all__ = [
    "OutboxClaimScope",
    "OutboxClaimScopeName",
    "build_outbox_claim_scopes",
    "build_scoped_outbox_engine",
    "clear_scoped_outbox_engine_cache",
]
