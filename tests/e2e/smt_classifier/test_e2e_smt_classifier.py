"""
SMT 粗分机 E2E 测试套件

测试 SMT 粗分机简化插件与 Mock 设备的端到端交互。

运行方式:
    # 运行所有 E2E 测试
    uv run pytest tests/e2e/smt_classifier/test_e2e_smt_classifier.py -v

    # 仅运行特定测试
    uv run pytest tests/e2e/smt_classifier/test_e2e_smt_classifier.py::
      TestSmtClassifierE2EFlows::test_full_ok_flow -v

    # 带详细日志
    uv run pytest tests/e2e/smt_classifier/test_e2e_smt_classifier.py -v --log-cli-level=INFO
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from src.workline_plugins.simplified_smt_plugin import (
    SimplifiedSmtPlugin,
    SmtClassifierState,
)
from src.workline_runtime.types import PluginResult

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import httpx

logger = logging.getLogger(__name__)


# ==================== E2E 基础测试类 ====================


class TestSmtClassifierE2EBase:
    """SMT 粗分机 E2E 测试基类"""

    @pytest.fixture(autouse=True)
    async def setup(self, clean_mock_state: None) -> None:
        """每个测试前的设置"""

    async def trigger_arm_scan(
        self,
        client: httpx.AsyncClient,
        barcode: str,
        location: str = "STATION_INPUT1",
    ) -> dict[str, Any]:
        """触发 ARM 扫码事件"""
        import time

        payload: dict[str, Any] = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": location,
                "LotCode": barcode.split("-", maxsplit=1)[0] if "-" in barcode else barcode,
                "DateCode": barcode.split("-")[1] if "-" in barcode and len(barcode.split("-")) > 1 else None,
                "Qty": "100",
                "ProductNo": barcode.split("-")[3] if "-" in barcode and len(barcode.split("-")) > 3 else None,
                "MfrPN": "MFR002",
                "PONumber": "PO2026040901",
            },
        }
        # 移除 None 值
        payload["data"] = {k: v for k, v in payload["data"].items() if v is not None}

        response = await client.post(
            "/api/v1/callback/event",
            json=payload,
        )
        _ = response.raise_for_status()
        return response.json()

    async def trigger_arm_command_result(
        self,
        client: httpx.AsyncClient,
        command_code: str,
        result: str = "SUCCESS",
        device_code: str = "ARM01",
    ) -> dict[str, Any]:
        """触发 ARM 命令结果回调"""
        import time

        payload: dict[str, Any] = {
            "device_code": device_code,
            "command_code": command_code,
            "result": result,
            "finish_time": int(time.time() * 1000),
            "data": {
                "actual_qty": 1,
                "location": "STATION_PIPELINE_INPUT1",
                "pick_and_put_result": "PUT_FINISHED" if result == "SUCCESS" else "FAILED",
            },
            "timestamp": int(time.time()),
        }

        response = await client.post(
            "/api/v1/callback/result",
            json=payload,
        )
        _ = response.raise_for_status()
        return response.json()

    async def get_arm_status(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """获取机械臂状态"""
        response = await client.get("/api/v1/device/status")
        _ = response.raise_for_status()
        return response.json()


# ==================== 主要 E2E 测试场景 ====================


@pytest.mark.e2e
class TestSmtClassifierE2EFlows(TestSmtClassifierE2EBase):
    """SMT 粗分机 E2E 流程测试"""

    @pytest.fixture
    def plugin(self) -> SimplifiedSmtPlugin:
        """创建插件实例"""
        return SimplifiedSmtPlugin()

    @pytest.fixture
    def mock_inbox(self) -> MagicMock:
        """创建模拟 Inbox"""
        inbox = MagicMock()
        inbox.id = 5001
        inbox.kind = "DEVICE_EVENT"
        inbox.device_id = 1
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_plugin_key_and_version(self, plugin: SimplifiedSmtPlugin) -> None:
        """测试插件注册信息"""
        assert plugin.plugin_key == "simplified_smt"
        assert plugin.contract_version == "1.0"

    @pytest.mark.asyncio
    async def test_scan_completed_ok_flow(
        self,
        plugin: SimplifiedSmtPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试扫码完成 OK 流程

        流程:
        1. 扫码完成 → 验证条码 → 生成 PICK_AND_PUT 命令
        """
        logger.info("=" * 60)
        logger.info("开始测试: 扫码完成 OK 流程")
        logger.info("=" * 60)

        # Step 1: 扫码完成事件
        logger.info("Step 1: 发送扫码完成事件")
        mock_plugin_context.session.status = "NEW"
        mock_inbox.payload_json = {
            "event_type": "SCAN_COMPLETED",
            "device_code": "ARM01",
            "timestamp": 1702627300000,
            "data": {
                "location": "STATION_INPUT1",
                "LotCode": "LOTABC123",
                "DateCode": "20250317",
                "Qty": "100",
                "ProductNo": "PROD001",
                "MfrPN": "MFR001",
                "PONumber": "PO001",
            },
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "scan_ok"
        assert result.context_patch["step_code"] == SmtClassifierState.WAITING_PICK_PLACE
        assert len(result.commands) == 1
        assert result.commands[0].action == "PICK_AND_PUT"
        # CommandIntent 只有 target_device_id，没有 device_role
        assert result.commands[0].target_device_id > 0

        logger.info(f"✓ 测试通过: transition={result.transition}, commands={len(result.commands)}")

    @pytest.mark.asyncio
    async def test_scan_invalid_barcode(
        self,
        plugin: SimplifiedSmtPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试无效条码流程"""
        logger.info("=" * 60)
        logger.info("开始测试: 无效条码流程")
        logger.info("=" * 60)

        mock_plugin_context.session.status = "NEW"
        mock_inbox.payload_json = {
            "event_type": "SCAN_COMPLETED",
            "device_code": "ARM01",
            "timestamp": 1702627300000,
            "data": {
                "location": "STATION_INPUT1",
                "LotCode": "X",  # 太短，无效
            },
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "scan_ng"
        assert result.failure is not None
        assert result.failure.code == "BARCODE_INVALID"

        logger.info(f"✓ 测试通过: 正确识别无效条码, failure={result.failure.message}")

    @pytest.mark.asyncio
    async def test_pick_success_flow(
        self,
        plugin: SimplifiedSmtPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试抓取成功流程"""
        logger.info("=" * 60)
        logger.info("开始测试: 抓取成功流程")
        logger.info("=" * 60)

        mock_plugin_context.session.status = "WAITING_PICK_PLACE"
        mock_plugin_context.session.context_json = {
            "barcode": "LOTABC123",
            "location": "STATION_INPUT1",
            "device_code": "ARM01",
            "step_code": SmtClassifierState.WAITING_PICK_PLACE,
        }

        # 设置命令结果的 Inbox
        mock_inbox.kind = "COMMAND_RESULT"
        mock_inbox.payload_json = {
            "device_code": "ARM01",
            "command_code": "CMD-001",
            "task_type": "PICK_AND_PUT",  # 必需：命令类型
            "result": "SUCCESS",
            "finish_time": 1702627250000,
            "data": {
                "actual_qty": 1,
                "location": "STATION_PIPELINE_INPUT1",
                "pick_and_put_result": "PUT_FINISHED",
            },
        }

        result = await plugin.on_command_result(mock_plugin_context, mock_inbox)

        assert result.transition == "pick_ok"
        assert result.context_patch["step_code"] == SmtClassifierState.WAITING_CONVEYOR
        assert len(result.commands) == 1
        assert result.commands[0].action == "MOVE_FORWARD"

        logger.info(f"✓ 测试通过: transition={result.transition}, next_command=MOVE_FORWARD")

        logger.info(f"✓ 测试通过: transition={result.transition}, next_command=MOVE_FORWARD")


# ==================== Mock 服务交互测试 ====================


@pytest.mark.e2e
@pytest.mark.integration
class TestSmtClassifierE2EMockInteractions(TestSmtClassifierE2EBase):
    """Mock 服务交互测试 - 验证真实 HTTP 通信"""

    @pytest.mark.asyncio
    async def test_arm01_health_check(self, arm01_client: httpx.AsyncClient) -> None:
        """测试 ARM01 Mock 根路径"""
        response = await arm01_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert "device_code" in data
        logger.info(f"✓ ARM01 Mock 健康检查通过: {data['device_code']}")

    @pytest.mark.asyncio
    async def test_pipeline_health_check(self, pipeline_client: httpx.AsyncClient) -> None:
        """测试 Pipeline Mock 根路径"""
        response = await pipeline_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        logger.info(f"✓ Pipeline Mock 健康检查通过: {data.get('device_code', 'PIPELINE')}")

    @pytest.mark.asyncio
    async def test_arm01_receive_command_via_mock_direct(
        self,
        arm01_client: httpx.AsyncClient,
    ) -> None:
        """直接向 ARM01 Mock 发送指令"""
        import time

        command_payload: dict[str, Any] = {
            "command_code": f"E2E-CMD-{int(time.time())}",
            "task_type": "PICK_AND_PUT",
            "priority": 1,
            "timeout": 30,
            "params": {
                "source_loc": "STATION_INPUT1",  # 使用具体的 location_id
                "target_loc": "STATION_PIPELINE1_INPUT1",  # 使用具体的 location_id
                "execution_time": 1,
            },
            "timestamp": int(time.time()),
        }

        response = await arm01_client.post(
            "/api/v1/device/command",
            json=command_payload,
        )

        assert response.status_code == 200
        ack = response.json()
        assert ack["code"] == 200
        logger.info(f"✓ ARM01 接收指令成功: {ack['message']}")

        # 等待执行完成
        await asyncio.sleep(2)

    @pytest.mark.asyncio
    async def test_pipeline_material_arrived_event(
        self,
        wes_client: httpx.AsyncClient,
    ) -> None:
        """测试 Pipeline 上报物料到达事件（符合白皮书规范）

        流程：
        1. Pipeline 检测到物料到达
        2. Pipeline 主动调用 WES 的 /api/v1/callback/event 上报事件
        3. WES 返回 200 OK（不含业务指令）
        4. WES 异步决策后下发下一步命令
        """
        import time

        logger.info("=" * 60)
        logger.info("开始测试: Pipeline 上报物料到达事件")
        logger.info("=" * 60)

        # Pipeline 作为客户端，调用 WES 的回调接口
        event_payload: dict[str, Any] = {
            "device_code": "PIPELINE01",
            "event_type": "MATERIAL_ARRIVED",
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": "STATION_INPUT1",
            },
        }

        response = await wes_client.post(
            "/api/v1/callback/event",
            json=event_payload,
        )

        assert response.status_code == 200
        logger.info("✓ Pipeline 上报物料到达事件成功")

        # WES 应立即返回 ACK，不包含具体的动作指令
        result = response.json()
        assert result.get("code") == 200 or result.get("message") == "Event received"
        logger.info(f"  WES 响应: {result}")

        logger.info("✓ 测试通过: 符合白皮书 3.3.1 传感器触发模式规范")

        # 等待扫描完成和回调
        await asyncio.sleep(2)


# ==================== 错误处理测试 ====================


@pytest.mark.e2e
class TestSmtClassifierE2EErrorHandling(TestSmtClassifierE2EBase):
    """错误处理测试"""

    @pytest.fixture
    def plugin(self) -> SimplifiedSmtPlugin:
        """创建插件实例"""
        return SimplifiedSmtPlugin()

    @pytest.fixture
    def mock_inbox(self) -> MagicMock:
        """创建模拟 Inbox"""
        inbox = MagicMock()
        inbox.id = 5001
        inbox.kind = "DEVICE_EVENT"
        inbox.device_id = 1
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_estop_handling(
        self,
        plugin: SimplifiedSmtPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试急停事件处理"""
        logger.info("=" * 60)
        logger.info("开始测试: 急停事件处理")
        logger.info("=" * 60)

        mock_plugin_context.session.status = "WAITING_PICK_PLACE"
        mock_inbox.payload_json = {
            "event_type": "ESTOP_PRESSED",
            "device_code": "ARM01",
            "timestamp": 1702627300000,
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.failure is not None
        assert result.failure.code == "ESTOP"
        assert result.failure.domain == "HARDWARE"

        logger.info(f"✓ 测试通过: 正确处理急停, failure={result.failure.message}")

    @pytest.mark.asyncio
    async def test_pick_failed_handling(
        self,
        plugin: SimplifiedSmtPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试抓取失败处理"""
        logger.info("=" * 60)
        logger.info("开始测试: 抓取失败处理")
        logger.info("=" * 60)

        mock_plugin_context.session.status = "WAITING_PICK_PLACE"
        mock_plugin_context.session.context_json = {
            "barcode": "LOTABC123",
            "step_code": SmtClassifierState.WAITING_PICK_PLACE,
        }

        # 构造命令结果的 Inbox payload
        mock_inbox.kind = "COMMAND_RESULT"
        mock_inbox.payload_json = {
            "command_code": "CMD-001",
            "device_code": "ARM01",
            "task_type": "PICK_AND_PUT",  # 必需：命令类型
            "result": "FAILED",
            "finish_time": 1702627250000,
            # error_detail 会被合并到顶层
            "error_detail": {
                "error_code": "2002",  # 搬运失败
                "error_message": "机械臂搬运失败",
            },
            # 顶层字段（从 error_detail 合并）
            "error_code": "2002",
            "error_message": "机械臂搬运失败",
        }

        result = await plugin.on_command_result(mock_plugin_context, mock_inbox)

        # 非尺寸/厚度错误会返回 failure
        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "2002"

        logger.info(f"✓ 测试通过: 正确处理抓取失败, failure={result.failure.message}")


# ==================== 测试标记 ====================


def pytest_configure(config: pytest.Config) -> None:
    """配置 pytest"""
    config.addinivalue_line(
        "markers",
        "e2e: marks tests as end-to-end tests (deselect with '-m \"not e2e\"')",
    )
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require running services)",
    )
