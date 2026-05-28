from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.wms_integration.services.exceptions import (
    WmsBusinessRejectedError,
    WmsEvidencePersistenceError,
    WmsUnavailableError,
)
from src.workline_plugins.smt_classifier.plugin import SmtClassifierPlugin
from src.workline_runtime.runtime_intent import BlockScope
from src.workline_runtime.services import WorklineRuntimeServices


@pytest.fixture
def plugin():
    return SmtClassifierPlugin()


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.services = WorklineRuntimeServices()
    # Mock ctx.next to return MagicMocks that can be inspected
    ctx.next.block = MagicMock(return_value="mock_block_intent")
    ctx.next.mark_ng = MagicMock(return_value="mock_mark_ng_intent")
    ctx.next.update_context = MagicMock(return_value="mock_update_context_intent")
    ctx.next.command = MagicMock(return_value="mock_command_intent")
    return ctx


@pytest.fixture
def mock_scan_inbox():
    inbox = MagicMock()
    inbox.trace_id = "trace-wms-001"
    inbox.payload_json = {
        "event_code": "SCAN_COMPLETED",
        "device_code": "SCANNER_01",
        "data": {
            "location": "CONVEYOR_LOC",
            "PkgID": "PKG_123",
            "HHPN": "HHP001",
            "MfrPN": "MFR001",
            "Qty": "100",
            "DateCode": "20260101",
            "LotCode": "LOT001",
        },
    }
    return inbox


@pytest.mark.asyncio
async def test_smt_scan_wms_unavailable_returns_runtime_hold(plugin, mock_ctx, mock_scan_inbox):
    """
    测试：当 WMS 返回不可用 (熔断/超时/5xx) 时，
    插件会生成一个 RuntimeHold Intent 阻塞当前流程。
    """
    wms_inventory_client = MagicMock()
    wms_inventory_client.query_inventory = AsyncMock(
        side_effect=WmsUnavailableError(
            message="Mock WMS is down",
            evidence_key="EVD-1234",
            http_status=503,
            reason_code="WMS_UNAVAILABLE",
            operation_name="query_inventory",
        )
    )
    mock_ctx.services = WorklineRuntimeServices(wms_inventory_client=wms_inventory_client)

    result = await plugin.handle_scan_completed(mock_ctx, mock_scan_inbox)

    # 期待返回的是 block_intent (即产生 RuntimeHold 的意图)
    assert result == "mock_block_intent"

    query_request = wms_inventory_client.query_inventory.await_args.args[0]
    assert query_request.sku == "HHP001"
    assert query_request.trace_id == "trace-wms-001"
    assert query_request.request_id

    # 验证 block 被正确构造，并保留 WMS evidence 以便运维反查 WmsCallEvidence。
    mock_ctx.next.block.assert_called_once_with(
        scope=BlockScope.MATERIAL,
        reason_code="WMS_UNAVAILABLE",
        message="Mock WMS is down",
        suggested_action="WMS 同步调用失败，请检查 WMS 状态或通过断路器恢复",
        payload={
            "operation_name": "query_inventory",
            "evidence_key": "EVD-1234",
            "reason_code": "WMS_UNAVAILABLE",
            "http_status": 503,
            "trace_id": "trace-wms-001",
            "request_id": query_request.request_id,
        },
    )


@pytest.mark.asyncio
async def test_smt_scan_wms_business_reject_returns_ng(plugin, mock_ctx, mock_scan_inbox):
    """
    测试：当 WMS 返回业务拒绝时（如物料锁定、过期），
    插件会按照业务失败流程流转（生成 NG 意图并继续流转到异常排出口）。
    """
    wms_inventory_client = MagicMock()
    wms_inventory_client.query_inventory = AsyncMock(
        side_effect=WmsBusinessRejectedError(
            message="Material is expired",
            evidence_key="EVD-5678",
            http_status=409,
            reason_code="WMS_BUSINESS_REJECTED",
            operation_name="query_inventory",
        )
    )
    mock_ctx.services = WorklineRuntimeServices(wms_inventory_client=wms_inventory_client)

    result = await plugin.handle_scan_completed(mock_ctx, mock_scan_inbox)

    query_request = wms_inventory_client.query_inventory.await_args.args[0]
    assert query_request.sku == "HHP001"
    assert query_request.trace_id == "trace-wms-001"
    assert query_request.request_id

    # 返回值应为一个意图列表
    assert isinstance(result, list)
    assert "mock_mark_ng_intent" in result

    mock_ctx.next.mark_ng.assert_called_once_with(
        reason_code="WMS_REJECTED",
        message="Material is expired",
        payload={
            "barcode": "PKG_123",
            "location": "CONVEYOR_LOC",
            "operation_name": "query_inventory",
            "evidence_key": "EVD-5678",
            "reason_code": "WMS_BUSINESS_REJECTED",
            "http_status": 409,
            "trace_id": "trace-wms-001",
            "request_id": query_request.request_id,
        },
    )


@pytest.mark.asyncio
async def test_smt_scan_wms_evidence_persistence_error_returns_system_diagnostic_hold(
    plugin, mock_ctx, mock_scan_inbox
):
    """
    测试：当 WMS 已成功但本地 evidence/breaker 留痕失败时，
    插件应进入系统诊断暂停，并保留 WMS evidence 反查字段。
    """
    wms_inventory_client = MagicMock()
    wms_inventory_client.query_inventory = AsyncMock(
        side_effect=WmsEvidencePersistenceError(
            message="WMS 已返回成功，但本地 evidence/breaker 成功留痕失败",
            evidence_key="EVD-PERSIST",
            http_status=200,
            reason_code="WMS_EVIDENCE_PERSISTENCE_FAILED",
            operation_name="query_inventory",
        )
    )
    mock_ctx.services = WorklineRuntimeServices(wms_inventory_client=wms_inventory_client)

    result = await plugin.handle_scan_completed(mock_ctx, mock_scan_inbox)

    assert result == "mock_block_intent"

    query_request = wms_inventory_client.query_inventory.await_args.args[0]
    assert query_request.sku == "HHP001"
    assert query_request.trace_id == "trace-wms-001"
    assert query_request.request_id

    mock_ctx.next.block.assert_called_once_with(
        scope=BlockScope.WORKLINE,
        reason_code="WMS_EVIDENCE_PERSISTENCE_FAILED",
        message="WMS 已返回成功，但本地 evidence/breaker 成功留痕失败",
        suggested_action="检查 WMS evidence/breaker 本地持久化状态",
        payload={
            "operation_name": "query_inventory",
            "evidence_key": "EVD-PERSIST",
            "reason_code": "WMS_EVIDENCE_PERSISTENCE_FAILED",
            "http_status": 200,
            "trace_id": "trace-wms-001",
            "request_id": query_request.request_id,
        },
    )
