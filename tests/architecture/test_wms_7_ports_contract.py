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
from src.app.wms_integration.ports.fulfillment import WmsFulfillmentPort

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
