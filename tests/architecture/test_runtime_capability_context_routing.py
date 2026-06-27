"""RuntimeCapabilityContext 路由测试 (Phase 1 CEO-009 / Packet D)。

主计划 §3.5.1 + H2: inbound normalizer 不可注入业务 capability, 只允许
`src.app.runtime.orchestration.consumers` 通过 RuntimeCapabilityContext.get_inbound_normalizer()
访问 InboundNormalizerRegistry。
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


def test_get_inbound_normalizer_from_allowed_caller():
    """caller_module 命中 allowlist 时正常返回 normalizer instance。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    ctx = RuntimeCapabilityContext(cap_reg, inbound_registry=inbound_reg)
    inst = ctx.get_inbound_normalizer(
        WmsEventPort,
        caller_module="src.app.runtime.orchestration.consumers.runtime_inbox_consumer",
    )
    assert inst is not None


def test_get_inbound_normalizer_from_allowed_package_root():
    """caller_module 精确等于 consumers 包名时正常返回 normalizer instance。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    ctx = RuntimeCapabilityContext(cap_reg, inbound_registry=inbound_reg)
    inst = ctx.get_inbound_normalizer(
        WmsEventPort,
        caller_module="src.app.runtime.orchestration.consumers",
    )
    assert inst is not None


def test_get_inbound_normalizer_rejects_business_capability_caller():
    """业务 capability 调用抛 PermissionError。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    ctx = RuntimeCapabilityContext(cap_reg, inbound_registry=inbound_reg)
    with pytest.raises(PermissionError) as exc_info:
        ctx.get_inbound_normalizer(
            WmsEventPort,
            caller_module="src.app.workline.runtime.workline_capability",
        )
    assert "inbound normalizer 不可注入业务 capability" in str(exc_info.value)


def test_get_inbound_normalizer_rejects_sibling_module_with_consumers_prefix():
    """`consumers_fake` 这类 sibling module 不能绕过 allowlist 前缀检查。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    ctx = RuntimeCapabilityContext(cap_reg, inbound_registry=inbound_reg)
    with pytest.raises(PermissionError) as exc_info:
        ctx.get_inbound_normalizer(
            WmsEventPort,
            caller_module="src.app.runtime.orchestration.consumers_fake",
        )
    assert "inbound normalizer 不可注入业务 capability" in str(exc_info.value)


def test_get_inbound_normalizer_rejects_unregistered_port():
    """inbound_registry 未注册该 port 时抛 KeyError。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    ctx = RuntimeCapabilityContext(cap_reg, inbound_registry=inbound_reg)
    with pytest.raises(KeyError):
        ctx.get_inbound_normalizer(
            WmsEventPort,
            caller_module="src.app.runtime.orchestration.consumers.runtime_inbox_consumer",
        )


def test_get_inbound_normalizer_without_registry_raises():
    """context 构造时未传 inbound_registry 时调用 get_inbound_normalizer 抛 RuntimeError。"""
    cap_reg = CapabilityPortRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    ctx = RuntimeCapabilityContext(cap_reg)  # no inbound_registry
    with pytest.raises(RuntimeError) as exc_info:
        ctx.get_inbound_normalizer(
            WmsEventPort,
            caller_module="src.app.runtime.orchestration.consumers.runtime_inbox_consumer",
        )
    assert "inbound_registry" in str(exc_info.value)
