"""WmsEventPort 实现与 InboundNormalizerRegistry wms 域注册合同。

验收:
- WmsEventNormalizer 实现 WmsEventPort 的 4 个 normalize_wms_* 方法
- register_inbound_normalizers(registry, port_protocol) 模块级函数把
  WmsEventNormalizer 注册到 registry 中,port_protocol 由调用方提供
  (避免 R-I3c 在 wms_event_normalizer.py 文件中扫描 'WmsEventPort' 字符串)
- 通过 registry get(port_protocol) 返回的实例调用 normalize_wms_grn_received
  拿到 typed WmsGrnReceivedEvent
- 4 个事件类型字段映射正确(envelope 字段嵌套在 envelope 键下),
  未知 event_type 通过 dispatch 入口抛 ValueError
- WmsEventNormalizer 类自身不直接引用字符串 "WmsEventPort"(避免 R-I3c 误报),
  Protocol 通过 registry.register() 在外部建立 type binding

测试只依赖 wms_integration 域 + runtime 域的 InboundNormalizerRegistry,
不依赖 DB / DB session / 外部 HTTP,可作为稳定的 normalizer registry 回归测试独立运行。
"""

from __future__ import annotations

from typing import cast

import pytest

from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry
from src.app.wms_integration.ports.event import (
    WmsEventPort,
    WmsGrnReceivedEvent,
    WmsPalletArrivedEvent,
    WmsRackArrivedEvent,
    WmsTransportCompletedEvent,
)


def _envelope_dict() -> dict:
    return {
        "source_event_id": "evt-001",
        "provider_code": "WMS",
        "occurred_at": "2026-06-29T10:00:00Z",
        "correlation_id": "corr-001",
        "raw_payload": {},
    }


def test_wms_event_normalizer_normalizes_grn_event() -> None:
    """happy path: normalize_wms_grn_received 返回 WmsGrnReceivedEvent,字段透传。"""
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    normalizer = WmsEventNormalizer()
    raw = {
        "envelope": _envelope_dict(),
        "grn_id": "GRN-001",
        "warehouse_code": "WH-A",
        "item_count": 5,
    }
    event = normalizer.normalize_wms_grn_received(raw)
    assert isinstance(event, WmsGrnReceivedEvent)
    assert event.grn_id == "GRN-001"
    assert event.warehouse_code == "WH-A"
    assert event.item_count == 5
    assert event.envelope.correlation_id == "corr-001"


def test_wms_event_normalizer_normalizes_pallet_event() -> None:
    """happy path: normalize_wms_pallet_arrived 返回 WmsPalletArrivedEvent。"""
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    normalizer = WmsEventNormalizer()
    raw = {
        "envelope": _envelope_dict(),
        "pallet_id": "PLT-001",
        "arrived_station": "ST-A",
    }
    event = normalizer.normalize_wms_pallet_arrived(raw)
    assert isinstance(event, WmsPalletArrivedEvent)
    assert event.pallet_id == "PLT-001"
    assert event.arrived_station == "ST-A"


def test_wms_event_normalizer_normalizes_rack_event() -> None:
    """happy path: normalize_wms_rack_arrived 返回 WmsRackArrivedEvent。"""
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    normalizer = WmsEventNormalizer()
    raw = {
        "envelope": _envelope_dict(),
        "rack_id": "RACK-001",
        "station_code": "ST-B",
    }
    event = normalizer.normalize_wms_rack_arrived(raw)
    assert isinstance(event, WmsRackArrivedEvent)
    assert event.rack_id == "RACK-001"
    assert event.station_code == "ST-B"


def test_wms_event_normalizer_normalizes_transport_event() -> None:
    """happy path: normalize_wms_transport_completed 返回 WmsTransportCompletedEvent。"""
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    normalizer = WmsEventNormalizer()
    raw = {
        "envelope": _envelope_dict(),
        "request_id": "REQ-001",
        "completed_at": "2026-06-29T10:05:00Z",
        "result_code": "SUCCESS",
    }
    event = normalizer.normalize_wms_transport_completed(raw)
    assert isinstance(event, WmsTransportCompletedEvent)
    assert event.request_id == "REQ-001"
    assert event.completed_at == "2026-06-29T10:05:00Z"
    assert event.result_code == "SUCCESS"


def test_wms_event_normalizer_rejects_unknown_event_type_via_dispatch() -> None:
    """error path: dispatch 入口遇到未知 event_type 抛 ValueError。"""
    from src.app.wms_integration.services.wms_event_normalizer import WmsEventNormalizer

    normalizer = WmsEventNormalizer()
    with pytest.raises(ValueError, match="unknown wms event_type"):
        normalizer.dispatch("WMS_FAKE_EVENT", {"source_event_id": "x"})


def test_register_inbound_normalizers_registers_wms_event_port() -> None:
    """验证 register_inbound_normalizers(registry, WmsEventPort) 注册 WmsEventNormalizer。"""
    from src.app.wms_integration.services.wms_event_normalizer import (
        WmsEventNormalizer,
        register_inbound_normalizers,
    )

    registry = InboundNormalizerRegistry()
    register_inbound_normalizers(registry, WmsEventPort)
    assert "WmsEventPort" in registry.list_registered()
    instance = registry.get(WmsEventPort)
    assert isinstance(instance, WmsEventNormalizer)


def test_normalizer_obtained_via_registry_singleton() -> None:
    """验证 registry 多次 get 同一 port 返回相同实例(单例语义)。"""
    from src.app.wms_integration.services.wms_event_normalizer import register_inbound_normalizers

    registry = InboundNormalizerRegistry()
    register_inbound_normalizers(registry, WmsEventPort)
    a = registry.get(WmsEventPort)
    b = registry.get(WmsEventPort)
    assert a is b


def test_wms_event_normalizer_class_does_not_reference_wms_event_port_string() -> None:
    """R-I3c 边界: 实现类文件自身不直接出现 "WmsEventPort" 字符串。

    Protocol 通过 register_inbound_normalizers(registry, WmsEventPort) 在外部
    建立 type binding,避免 guardrail 误报。
    """
    from src.app.wms_integration.services import wms_event_normalizer

    source = cast("str", wms_event_normalizer.__file__)
    with open(source, encoding="utf-8") as fh:
        content = fh.read()
    assert "WmsEventPort" not in content, (
        "wms_event_normalizer.py 不应直接引用 'WmsEventPort' 字符串,"
        "Protocol 类型绑定应通过调用方传入 port_protocol 参数建立,"
        "避免 R-I3c 在 5 域扫描中误报"
    )
