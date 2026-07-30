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
from src.app.wms_integration.ports.reconciliation_query import WmsReconciliationQueryPort


def test_wms_fulfillment_uses_only_operation_specific_typed_definitions():
    fulfillment_operations = tuple(
        operation for operation in EFFECT_OPERATIONS if operation.identity.startswith("wms.fulfillment.")
    )

    assert len(fulfillment_operations) == 10
    assert all(operation.request_model is not operation.result_model for operation in fulfillment_operations)
    assert all(operation.request_model.model_config["extra"] == "forbid" for operation in fulfillment_operations)
    assert all(operation.result_model.model_config["extra"] == "forbid" for operation in fulfillment_operations)


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
    """保留 4 个真实 Protocol；入站 normalizer 由 callback admission 直接组合。"""
    from src.app.wms_integration.ports.inventory_transaction import WmsInventoryTransactionPort
    from src.app.wms_integration.ports.master_data import WmsMasterDataPort
    from src.app.wms_integration.ports.query_execution import WmsQueryExecutionPort

    all_ports = [
        WmsMasterDataPort,
        WmsQueryExecutionPort,
        WmsInventoryTransactionPort,
        WmsReconciliationQueryPort,
    ]
    assert len(all_ports) == 4
    for port in all_ports:
        assert issubclass(port, Protocol)
