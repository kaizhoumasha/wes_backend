"""WMS Protocol 与 operation-specific boundary contract tests。

每个仍存活的 Protocol 必须满足:
- Protocol 抽象性 (typing.Protocol 子类)
- 所有方法有 docstring
- 数据类有 docstring

履约由 operation registry 的 E07–E16 typed Definition 唯一表达，不再计为粗粒度 Protocol。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.app.wms_integration.ports.document import WmsDocumentPort
from src.app.wms_integration.ports.event import InboundEventPort, WmsEventPort
from src.app.wms_integration.ports.reconciliation_query import WmsReconciliationQueryPort


def test_wms_document_port_is_protocol():
    """WmsDocumentPort 必须是 typing.Protocol 子类。"""
    assert issubclass(WmsDocumentPort, Protocol)


def test_wms_document_protocol_signatures():
    """WmsDocumentPort 5 方法签名与 PO 行级 GRN 合同一致。"""
    methods = ["get_grn", "get_pick_order", "get_outbound_order", "get_wave", "get_task_snapshot"]
    for name in methods:
        assert hasattr(WmsDocumentPort, name), f"missing method: {name}"
        method = getattr(WmsDocumentPort, name)
        assert callable(method)


def test_wms_document_port_have_docstrings():
    """WmsDocumentPort 类和所有方法必须含 docstring。"""
    assert WmsDocumentPort.__doc__, "WmsDocumentPort class needs docstring"
    for name in ["get_grn", "get_pick_order", "get_outbound_order", "get_wave", "get_task_snapshot"]:
        method = getattr(WmsDocumentPort, name)
        assert method.__doc__, f"method {name} needs docstring"


def test_wms_document_data_classes_are_pydantic():
    """WmsDocumentPort 关联的 5 数据类必须是 BaseModel 子类且含 docstring。"""
    from src.app.wms_integration.ports.document import (
        WmsGrnInfo,
        WmsOutboundOrder,
        WmsPickOrder,
        WmsTaskSnapshot,
        WmsWave,
    )

    for cls in [WmsGrnInfo, WmsPickOrder, WmsOutboundOrder, WmsWave, WmsTaskSnapshot]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"


def test_wms_fulfillment_uses_only_operation_specific_typed_definitions():
    fulfillment_operations = tuple(
        operation for operation in EFFECT_OPERATIONS if operation.identity.startswith("wms.fulfillment.")
    )

    assert len(fulfillment_operations) == 10
    assert all(operation.request_model is not operation.result_model for operation in fulfillment_operations)
    assert all(operation.request_model.model_config["extra"] == "forbid" for operation in fulfillment_operations)
    assert all(operation.result_model.model_config["extra"] == "forbid" for operation in fulfillment_operations)


def test_inbound_event_port_is_protocol():
    assert issubclass(InboundEventPort, Protocol)


def test_wms_event_port_is_protocol():
    assert issubclass(WmsEventPort, Protocol)


def test_wms_event_port_normalizer_signatures():
    methods = [
        "normalize_wms_grn_received",
        "normalize_wms_pallet_arrived",
        "normalize_wms_inventory_updated",
        "normalize_wms_pda_operation_recorded",
    ]
    for name in methods:
        assert hasattr(WmsEventPort, name), f"missing normalizer: {name}"


def test_wms_event_port_have_docstrings():
    assert WmsEventPort.__doc__, "WmsEventPort class needs docstring"
    assert InboundEventPort.__doc__, "InboundEventPort class needs docstring"
    for name in [
        "normalize_wms_grn_received",
        "normalize_wms_pallet_arrived",
        "normalize_wms_inventory_updated",
        "normalize_wms_pda_operation_recorded",
    ]:
        method = getattr(WmsEventPort, name)
        assert method.__doc__, f"normalizer {name} needs docstring"


def test_wms_event_data_classes_are_pydantic():
    from src.app.wms_integration.ports.event import (
        InboundEventEnvelope,
        WmsGrnReceivedEvent,
        WmsInventoryUpdatedEvent,
        WmsPalletArrivedEvent,
        WmsPdaOperationRecordedEvent,
    )

    for cls in [
        InboundEventEnvelope,
        WmsGrnReceivedEvent,
        WmsPalletArrivedEvent,
        WmsInventoryUpdatedEvent,
        WmsPdaOperationRecordedEvent,
    ]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"


def test_wms_reconciliation_query_port_is_protocol():
    assert issubclass(WmsReconciliationQueryPort, Protocol)


def test_wms_reconciliation_query_protocol_signatures():
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


def test_remaining_wms_protocol_boundaries_are_present():
    """保留 6 个真实 Protocol；履约由 operation-specific Definition 承接。"""
    from src.app.wms_integration.ports.inventory_transaction import WmsInventoryTransactionPort
    from src.app.wms_integration.ports.master_data import WmsMasterDataPort
    from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationPort

    all_ports = [
        WmsMasterDataPort,
        InventoryQueryOperationPort,
        WmsInventoryTransactionPort,
        WmsDocumentPort,
        WmsEventPort,
        WmsReconciliationQueryPort,
    ]
    assert len(all_ports) == 6
    for port in all_ports:
        assert issubclass(port, Protocol)
