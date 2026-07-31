"""Runtime capability port registry + H2/H3 contract test。

RuntimeCapabilityContext / CapabilityPortRegistry:
- H2: type guard 拒绝 inbound normalizer 注册
- factory pattern: 按需构造, 不暴露 implementation type
- capability 只拿 query/effect port, 不拿 inbound normalizer
"""

from __future__ import annotations

from typing import Protocol

import pytest

from src.app.runtime.capability_port_registry import (
    CapabilityPortRegistry,
    RuntimeCapabilityContext,
)
from src.app.wms_integration.ports.query_execution import WmsQueryExecutionPort


class _LocalQueryPort(Protocol):
    def lookup(self, object_key: str): ...


class _FakeLocalQueryPort:
    def lookup(self, object_key: str):
        return None


class _FakeInventoryQueryPort:
    async def execute(self, request):
        return []


class _LegacySyncInventoryQueryPort:
    def execute(self, request):
        return []


# ---- H2: type guard 拒绝 inbound normalizer ----


def test_registry_rejects_wms_event_port():
    """H2: WmsEventPort 是 inbound normalizer, 注册表拒绝。"""
    registry = CapabilityPortRegistry()

    class WmsEventPort:
        pass

    with pytest.raises(ValueError, match="inbound normalizer"):
        registry.register(WmsEventPort, lambda: WmsEventPort())


def test_registry_rejects_device_event_port():
    """H2: DeviceEventPort 是 inbound normalizer, 注册表拒绝。"""
    registry = CapabilityPortRegistry()

    class DeviceEventPort:
        pass

    with pytest.raises(ValueError, match="inbound normalizer"):
        registry.register(DeviceEventPort, lambda: DeviceEventPort())


def test_registry_rejects_inbound_event_port():
    """H2: InboundEventPort 是 inbound normalizer, 注册表拒绝。"""
    registry = CapabilityPortRegistry()

    class InboundEventPort:
        pass

    with pytest.raises(ValueError, match="inbound normalizer"):
        registry.register(InboundEventPort, lambda: InboundEventPort())


# ---- factory pattern + 正常注册 ----


def test_registry_register_and_get_query_port():
    """正常注册 query port + get 返回 factory 构造的实例。"""
    registry = CapabilityPortRegistry()
    registry.register(_LocalQueryPort, _FakeLocalQueryPort)
    port = registry.get(_LocalQueryPort)
    assert hasattr(port, "lookup")


def test_registry_get_unregistered_raises():
    """获取未注册 port 抛 KeyError。"""
    registry = CapabilityPortRegistry()
    with pytest.raises(KeyError, match="未注册"):
        registry.get(WmsQueryExecutionPort)


def test_registry_rejects_legacy_sync_implementation_for_async_protocol_method():
    registry = CapabilityPortRegistry()
    registry.register(WmsQueryExecutionPort, _LegacySyncInventoryQueryPort)

    with pytest.raises(TypeError, match="async Port contract"):
        registry.get(WmsQueryExecutionPort)


def test_registry_list_registered():
    """list_registered 返回已注册 port 名称列表。"""
    registry = CapabilityPortRegistry()
    registry.register(_LocalQueryPort, _FakeLocalQueryPort)
    registry.register(WmsQueryExecutionPort, _FakeInventoryQueryPort)
    assert sorted(registry.list_registered()) == ["WmsQueryExecutionPort", "_LocalQueryPort"]


def test_registry_is_registered():
    """is_registered 检查 port 是否已注册。"""
    registry = CapabilityPortRegistry()
    registry.register(_LocalQueryPort, _FakeLocalQueryPort)
    assert registry.is_registered(_LocalQueryPort)
    assert not registry.is_registered(WmsQueryExecutionPort)


# ---- RuntimeCapabilityContext ----


def test_capability_context_get_query_port():
    """RuntimeCapabilityContext.get_query_port 返回注册的 port 实例。"""
    registry = CapabilityPortRegistry()
    registry.register(_LocalQueryPort, _FakeLocalQueryPort)
    ctx = RuntimeCapabilityContext(registry)
    port = ctx.get_query_port(_LocalQueryPort)
    assert hasattr(port, "lookup")


def test_capability_context_get_effect_port():
    """RuntimeCapabilityContext.get_effect_port 返回注册的 port 实例。"""
    registry = CapabilityPortRegistry()
    registry.register(WmsQueryExecutionPort, _FakeInventoryQueryPort)
    ctx = RuntimeCapabilityContext(registry)
    port = ctx.get_effect_port(WmsQueryExecutionPort)
    assert hasattr(port, "execute")
