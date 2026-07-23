"""RuntimeCapabilityContext 路由测试。

inbound normalizer 不可注入业务 capability；RuntimeInbox 消费由
Celery task → RuntimeInboxProcessorBridge 独立承担。
"""

from __future__ import annotations

import pytest

from src.app.runtime.capability_port_registry import (
    CapabilityPortRegistry,
    RuntimeCapabilityContext,
)
from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry


def _dummy_wms_event_port():
    """测试用 WmsEventPort 假实现 (Protocol duck-typed)。"""

    class WmsEventPort:
        def normalize(self, raw):
            return raw

    return WmsEventPort()


def test_inbound_normalizer_registry_register_and_get():
    """正常注册后 get 返回 instance。"""
    reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return _dummy_wms_event_port()

    reg.register(WmsEventPort, factory)
    inst = reg.get(WmsEventPort)
    assert inst is not None
    assert len(factory_calls) == 1


def test_inbound_normalizer_registry_singleton_factory_called_once():
    """多次 get 同一 port 只调用 factory 一次。"""
    reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    call_count = {"n": 0}

    def factory():
        call_count["n"] += 1
        return _dummy_wms_event_port()

    reg.register(WmsEventPort, factory)
    reg.get(WmsEventPort)
    reg.get(WmsEventPort)
    assert call_count["n"] == 1


def test_inbound_normalizer_registry_unregistered_raises_keyerror():
    """未注册的 port get 时抛 KeyError。"""
    reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    with pytest.raises(KeyError) as exc_info:
        reg.get(WmsEventPort)
    assert "未注册" in str(exc_info.value)


def test_runtime_capability_module_has_no_inbound_consumer_context_factory():
    """capability registry 不再提供旧消费 facade 的专用 wiring。"""
    import src.app.runtime.capability_port_registry as capability_ports

    assert not hasattr(capability_ports, "create_inbound_normalizer_context")
    assert not hasattr(capability_ports, "InboundNormalizerContext")


def test_runtime_capability_context_does_not_expose_inbound_accessor():
    """通用 capability context 不暴露 inbound accessor, 业务方无法伪造 caller_module。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    ctx = RuntimeCapabilityContext(cap_reg)
    assert not hasattr(ctx, "get_inbound_normalizer")


def test_get_inbound_normalizer_rejects_business_capability_caller():
    """旧的 caller_module 字符串 guard 不存在, 业务 capability 无法调用 inbound accessor。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    ctx = RuntimeCapabilityContext(cap_reg)
    with pytest.raises(AttributeError):
        ctx.get_inbound_normalizer(  # type: ignore[attr-defined]
            WmsEventPort,
            caller_module="src.app.workline.runtime.workline_capability",
        )
