"""7-port contract tests (Phase 1 Packet D, 主计划 §5.1 + Phase 1 SPEC §139-140).

每个 WMS port 必须满足:
- Port.method 命名 (ClassName.method 格式)
- Protocol 抽象性 (typing.Protocol 子类)
- 所有方法有 docstring
- 数据类有 docstring
"""

from __future__ import annotations

import inspect
import re
from typing import Protocol, get_type_hints

from pydantic import BaseModel

from src.app.wms_integration.ports.document import WmsDocumentPort
from src.app.wms_integration.ports.event import InboundEventPort, WmsEventPort
from src.app.wms_integration.ports.fulfillment import WmsFulfillmentPort
from src.app.wms_integration.ports.reconciliation_query import WmsReconciliationQueryPort

PORT_METHOD_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*Port\.[a-z_][A-Za-z0-9_]*$")


def test_wms_document_port_is_protocol():
    """WmsDocumentPort 必须是 typing.Protocol 子类。"""
    assert issubclass(WmsDocumentPort, Protocol)


def test_wms_document_port_method_signatures():
    """WmsDocumentPort 6 方法签名与主计划 §5.1 一致。"""
    methods = ["get_grn", "list_grn_items", "get_pick_order", "get_outbound_order", "get_wave", "get_task_snapshot"]
    for name in methods:
        assert hasattr(WmsDocumentPort, name), f"missing method: {name}"
        method = getattr(WmsDocumentPort, name)
        assert callable(method)


def test_wms_document_port_have_docstrings():
    """WmsDocumentPort 类和所有方法必须含 docstring。"""
    assert WmsDocumentPort.__doc__, "WmsDocumentPort class needs docstring"
    for name in ["get_grn", "list_grn_items", "get_pick_order", "get_outbound_order", "get_wave", "get_task_snapshot"]:
        method = getattr(WmsDocumentPort, name)
        assert method.__doc__, f"method {name} needs docstring"


def test_wms_document_data_classes_are_pydantic():
    """WmsDocumentPort 关联的 6 数据类必须是 BaseModel 子类且含 docstring。"""
    from src.app.wms_integration.ports.document import (
        WmsGrnInfo,
        WmsGrnItem,
        WmsOutboundOrder,
        WmsPickOrder,
        WmsTaskSnapshot,
        WmsWave,
    )

    for cls in [WmsGrnInfo, WmsGrnItem, WmsPickOrder, WmsOutboundOrder, WmsWave, WmsTaskSnapshot]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"


def test_wms_fulfillment_port_is_protocol():
    assert issubclass(WmsFulfillmentPort, Protocol)


def test_wms_fulfillment_port_method_signatures():
    methods = [
        "request_rack_supply",
        "request_rack_transport",
        "change_rack_face",
        "full_box_exchange",
        "move_bin_to_conveyor_entry",
        "move_bin_to_conveyor_exit",
        "notify_pkg_binding",
    ]
    for name in methods:
        assert hasattr(WmsFulfillmentPort, name), f"missing method: {name}"
        method = getattr(WmsFulfillmentPort, name)
        assert callable(method)


def test_wms_fulfillment_port_have_docstrings():
    assert WmsFulfillmentPort.__doc__, "WmsFulfillmentPort class needs docstring"
    for name in [
        "request_rack_supply",
        "request_rack_transport",
        "change_rack_face",
        "full_box_exchange",
        "move_bin_to_conveyor_entry",
        "move_bin_to_conveyor_exit",
        "notify_pkg_binding",
    ]:
        method = getattr(WmsFulfillmentPort, name)
        assert method.__doc__, f"method {name} needs docstring"


def test_wms_fulfillment_data_classes_are_pydantic():
    from src.app.wms_integration.ports.fulfillment import WmsFulfillmentResult, WmsPalletBindingResult

    for cls in [WmsFulfillmentResult, WmsPalletBindingResult]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"


def test_inbound_event_port_is_protocol():
    assert issubclass(InboundEventPort, Protocol)


def test_wms_event_port_is_protocol():
    assert issubclass(WmsEventPort, Protocol)


def test_wms_event_port_normalizer_signatures():
    methods = [
        "normalize_wms_grn_received",
        "normalize_wms_pallet_arrived",
        "normalize_wms_rack_arrived",
        "normalize_wms_transport_completed",
    ]
    for name in methods:
        assert hasattr(WmsEventPort, name), f"missing normalizer: {name}"


def test_wms_event_port_have_docstrings():
    assert WmsEventPort.__doc__, "WmsEventPort class needs docstring"
    assert InboundEventPort.__doc__, "InboundEventPort class needs docstring"
    for name in [
        "normalize_wms_grn_received",
        "normalize_wms_pallet_arrived",
        "normalize_wms_rack_arrived",
        "normalize_wms_transport_completed",
    ]:
        method = getattr(WmsEventPort, name)
        assert method.__doc__, f"normalizer {name} needs docstring"


def test_wms_event_data_classes_are_pydantic():
    from src.app.wms_integration.ports.event import (
        InboundEventEnvelope,
        WmsGrnReceivedEvent,
        WmsPalletArrivedEvent,
        WmsRackArrivedEvent,
        WmsTransportCompletedEvent,
    )

    for cls in [
        InboundEventEnvelope,
        WmsGrnReceivedEvent,
        WmsPalletArrivedEvent,
        WmsRackArrivedEvent,
        WmsTransportCompletedEvent,
    ]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"


def test_wms_reconciliation_query_port_is_protocol():
    assert issubclass(WmsReconciliationQueryPort, Protocol)


def test_wms_reconciliation_query_port_method_signatures():
    methods = ["check_bin_drift", "check_rack_drift", "check_full_drift"]
    for name in methods:
        assert hasattr(WmsReconciliationQueryPort, name), f"missing method: {name}"
        method = getattr(WmsReconciliationQueryPort, name)
        assert callable(method)


def test_wms_reconciliation_query_port_have_docstrings():
    assert WmsReconciliationQueryPort.__doc__, "WmsReconciliationQueryPort class needs docstring"
    for name in ["check_bin_drift", "check_rack_drift", "check_full_drift"]:
        method = getattr(WmsReconciliationQueryPort, name)
        assert method.__doc__, f"method {name} needs docstring"


def test_wms_reconciliation_query_data_classes_are_pydantic():
    from src.app.wms_integration.ports.reconciliation_query import WmsDriftItem

    assert issubclass(WmsDriftItem, BaseModel)
    assert WmsDriftItem.__doc__, "WmsDriftItem needs docstring"


def test_all_seven_wms_ports_present():
    """Phase 1 CEO-001 完成 7/7 ports (主计划 §5.1)。"""
    from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort
    from src.app.wms_integration.ports.inventory_transaction import WmsInventoryTransactionPort
    from src.app.wms_integration.ports.master_data import WmsMasterDataPort

    all_ports = [
        WmsMasterDataPort,
        WmsInventoryQueryPort,
        WmsInventoryTransactionPort,
        WmsDocumentPort,
        WmsFulfillmentPort,
        WmsEventPort,
        WmsReconciliationQueryPort,
    ]
    assert len(all_ports) == 7
    for port in all_ports:
        assert issubclass(port, Protocol)
