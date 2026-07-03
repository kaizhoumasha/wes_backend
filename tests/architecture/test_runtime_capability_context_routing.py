"""RuntimeCapabilityContext 路由测试 (Phase 1 CEO-009 / Packet D)。

主计划 §3.5.1 + H2: inbound normalizer 不可注入业务 capability, 只允许
`src.app.runtime.orchestration.consumers` 通过内部 inbound context 访问
InboundNormalizerRegistry。
"""

from __future__ import annotations

import pytest

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.runtime import capability_port_registry as capability_ports
from src.app.runtime.capability_port_registry import (
    CapabilityPortRegistry,
    RuntimeCapabilityContext,
)
from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry
from src.app.wms_integration.ports.fulfillment import WmsFulfillmentPort
from src.app.wms_integration.ports.master_data import WmsMasterDataPort


def _dummy_wms_event_port():
    """测试用 WmsEventPort 假实现 (Protocol duck-typed)。"""

    class WmsEventPort:
        def normalize(self, raw):
            return raw

    return WmsEventPort()


def _create_inbound_normalizer_context(inbound_reg):
    assert hasattr(capability_ports, "create_inbound_normalizer_context")
    return capability_ports.create_inbound_normalizer_context(inbound_reg)


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


def test_inbound_context_returns_normalizer_for_consumer_wiring():
    """consumer 构造路径拿到内部 inbound context 后可获取 normalizer。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    RuntimeCapabilityContext(cap_reg)
    inbound_ctx = _create_inbound_normalizer_context(inbound_reg)
    inst = inbound_ctx.get_inbound_normalizer(WmsEventPort)
    assert inst is not None


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


def test_get_inbound_normalizer_rejects_spoofed_allowed_caller_string():
    """伪造合法 caller_module 字符串不能取得 inbound normalizer。"""
    cap_reg = CapabilityPortRegistry()
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_reg.register(WmsEventPort, _dummy_wms_event_port)

    ctx = RuntimeCapabilityContext(cap_reg)
    with pytest.raises(AttributeError):
        ctx.get_inbound_normalizer(  # type: ignore[attr-defined]
            WmsEventPort,
            caller_module="src.app.runtime.orchestration.consumers.runtime_inbox_consumer",
        )


def test_get_inbound_normalizer_rejects_unregistered_port():
    """consumer inbound context 未注册该 port 时抛 KeyError。"""
    inbound_reg = InboundNormalizerRegistry()
    WmsEventPort = type("WmsEventPort", (), {})
    inbound_ctx = _create_inbound_normalizer_context(inbound_reg)
    with pytest.raises(KeyError):
        inbound_ctx.get_inbound_normalizer(WmsEventPort)


def test_get_inbound_normalizer_without_registry_raises():
    """创建 inbound context 时必须显式提供 inbound_registry。"""
    cap_reg = CapabilityPortRegistry()
    ctx = RuntimeCapabilityContext(cap_reg)  # no inbound_registry
    assert not hasattr(ctx, "get_inbound_normalizer")
    with pytest.raises(RuntimeError) as exc_info:
        _create_inbound_normalizer_context(None)
    assert "inbound_registry" in str(exc_info.value)


def test_provider_profile_blocks_undeclared_query_port():
    """provider profile 未声明的 query port 不得进入 RuntimeCapabilityContext。"""

    cap_reg = CapabilityPortRegistry()
    cap_reg.register(WmsMasterDataPort, lambda: object())
    ctx = RuntimeCapabilityContext(
        cap_reg,
        allowed_query_capabilities=("WmsDocumentPort.get_grn",),
    )

    with pytest.raises(PermissionError, match="未声明 query capability"):
        ctx.get_query_port(WmsMasterDataPort)


def test_provider_profile_blocks_undeclared_query_port_before_registry_lookup():
    """provider profile 未声明时先拒绝 admission, 不暴露 registry 未注册细节。"""

    ctx = RuntimeCapabilityContext(
        CapabilityPortRegistry(),
        allowed_query_capabilities=("WmsDocumentPort.get_grn",),
    )

    with pytest.raises(PermissionError, match="未声明 query capability"):
        ctx.get_query_port(WmsMasterDataPort)


def test_provider_profile_blocks_undeclared_effect_port():
    """provider profile 未声明的 effect port 不得进入 RuntimeCapabilityContext。"""

    cap_reg = CapabilityPortRegistry()
    cap_reg.register(WmsFulfillmentPort, lambda: object())
    ctx = RuntimeCapabilityContext(
        cap_reg,
        allowed_effect_capabilities=("WmsInventoryTransactionPort.reserve_inventory",),
    )

    with pytest.raises(PermissionError, match="未声明 effect capability"):
        ctx.get_effect_port(WmsFulfillmentPort)


def test_provider_profile_allows_declared_ports():
    """provider profile 已声明的 query/effect port 可以进入 RuntimeCapabilityContext。"""

    class MasterDataPort:
        def get_material(self):
            return "material"

    class FulfillmentPort:
        def request_transport(self):
            return "transport"

    cap_reg = CapabilityPortRegistry()
    cap_reg.register(WmsMasterDataPort, MasterDataPort)
    cap_reg.register(WmsFulfillmentPort, FulfillmentPort)
    profile = ExternalContractProfile(
        provider_code="WMS",
        contract_version="2026-06-25",
        environment="sandbox",
        runtime_capabilities_query=["WmsMasterDataPort.get_material"],
        runtime_capabilities_effect=["WmsFulfillmentPort.request_transport"],
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_effect_timeout_seconds=30,
        timeout_retry_retry_backoff_seconds=[1, 2, 4],
        fixture_set_path="tests/fixtures/external_contracts/wms/default",
        fixture_set_required_cases=["success"],
    )

    ctx = RuntimeCapabilityContext.from_provider_profile(cap_reg, profile)

    assert ctx.get_query_port(WmsMasterDataPort).get_material() == "material"
    assert ctx.get_effect_port(WmsFulfillmentPort).request_transport() == "transport"


def test_provider_profile_proxy_blocks_undeclared_method_on_declared_port():
    """同一 port 上未声明的方法不能通过 provider-restricted proxy 调用。"""

    class FulfillmentPort:
        def request_transport(self):
            return "transport"

        def notify_pkg_binding(self):
            return "binding"

    cap_reg = CapabilityPortRegistry()
    cap_reg.register(WmsFulfillmentPort, FulfillmentPort)
    ctx = RuntimeCapabilityContext(
        cap_reg,
        allowed_effect_capabilities=("WmsFulfillmentPort.request_transport",),
    )

    effect_port = ctx.get_effect_port(WmsFulfillmentPort)

    assert effect_port.request_transport() == "transport"
    with pytest.raises(PermissionError, match="未声明 effect capability"):
        effect_port.notify_pkg_binding()
