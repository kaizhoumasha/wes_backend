"""
SMT 粗分机 E2E 测试套件

测试 SMT 粗分机插件与 Mock 设备的端到端交互。

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

from src.workline_plugins.smt_classifier.plugin import (
    SmtClassifierCommandType,
    SmtClassifierDeviceRole,
    SmtClassifierEventType,
    SmtClassifierPlugin,
    SmtClassifierStage,
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

    async def trigger_pipeline_scan(
        self,
        client: httpx.AsyncClient,
        barcode: str,
        result: str = "OK",
    ) -> dict[str, Any]:
        """触发 ARM01 扫码事件"""
        response = await client.post(
            "/debug/scan-completed",
            json={"barcode": barcode, "result": result},
        )
        response.raise_for_status()
        return response.json()

    async def trigger_pipeline_detect(
        self,
        client: httpx.AsyncClient,
        barcode: str,
        result: str = "OK",
    ) -> dict[str, Any]:
        """触发 ARM01 检测事件"""
        response = await client.post(
            "/debug/inspection-completed",
            json={
                "result": result,
                "barcode": barcode,
                "dimensions": {"length": 100.0, "width": 50.0, "height": 15.0},
            },
        )
        response.raise_for_status()
        return response.json()

    async def trigger_pipeline_thickness(
        self,
        client: httpx.AsyncClient,
        barcode: str,
        result: str = "OK",
    ) -> dict[str, Any]:
        """兼容旧测试命名，复用检测完成接口"""
        response = await client.post(
            "/debug/inspection-completed",
            json={
                "barcode": barcode,
                "result": result,
                "reel_thickness": "15.5",
            },
        )
        response.raise_for_status()
        return response.json()

    async def send_arm_command(
        self,
        client: httpx.AsyncClient,
        task_type: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送机械臂命令"""
        import time

        payload = {
            "command_code": f"CMD-{int(time.time() * 1000)}",
            "task_type": task_type,
            "priority": 1,
            "timeout": 30,
            "params": params or {},
            "timestamp": int(time.time()),
        }
        response = await client.post(
            "/api/v1/device/command",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def get_arm_status(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """获取机械臂状态"""
        response = await client.get("/api/v1/device/status")
        response.raise_for_status()
        return response.json()

    async def get_pipeline_status(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """获取流水线状态"""
        response = await client.get("/api/v1/device/status")
        response.raise_for_status()
        return response.json()


# ==================== 主要 E2E 测试场景 ====================


@pytest.mark.e2e
class TestSmtClassifierE2EFlows(TestSmtClassifierE2EBase):
    """SMT 粗分机 E2E 流程测试"""

    @pytest.fixture
    def plugin(self) -> SmtClassifierPlugin:
        """创建插件实例"""
        return SmtClassifierPlugin()

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
    async def test_full_ok_flow(
        self,
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
        pipeline_client: httpx.AsyncClient,
        arm01_client: httpx.AsyncClient,
        arm02_client: httpx.AsyncClient,
    ) -> None:
        """测试完整 OK 流程

        流程:
        1. 扫码 OK 事件 -> 等待检测
        2. 检测 OK 事件 -> 生成 PICK_AND_PUT 命令 (进料)
        3. 命令结果 SUCCESS -> 生成 MOVE_FORWARD 命令
        4. 流水线完成 -> 生成 PICK_AND_PUT 命令 (出料)
        5. 出料完成 -> 流程结束
        """
        logger.info("=" * 60)
        logger.info("开始测试: 完整 OK 流程")
        logger.info("=" * 60)

        # Step 1: 扫码 OK 事件
        logger.info("Step 1: 发送扫码 OK 事件")
        mock_plugin_context.session.status = "NEW"
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "E2E-TEST-001",
            "scan_result": "OK",
            "location_id": "LEFT_STATION_INPUT",
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "scan_ok"
        assert result.context_patch["stage"] == SmtClassifierStage.WAITING_INSPECTION.value
        assert result.context_patch["scan_result"] == "OK"
        logger.info("✓ Step 1 完成: 扫码 OK，进入等待检测阶段")

        # Step 2: 检测 OK 事件
        logger.info("Step 2: 发送检测 OK 事件")
        mock_plugin_context.session.context_json = result.context_patch
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.INSPECTION_COMPLETED.value,
            "inspection_result": "OK",
            "location_id": "LEFT_STATION_DETECT",
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "inspection_ok"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.MOVE_FORWARD.value
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        logger.info("✓ Step 2 完成: 检测 OK，生成流水线前进命令")

        # Step 3: 模拟流水线传输完成
        logger.info("Step 3: 模拟流水线传输完成")
        mock_plugin_context.session.context_json = {
            **result.context_patch,
            "source_type": "PIPELINE_PLATFORM",
            "target_type": "PIPELINE_PLATFORM",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.MOVE_FORWARD.value,
            "result": "SUCCESS",
            "data": {"actual_source": "PIPELINE_IN", "actual_target": "PIPELINE_OUT"},
        }

        result = await plugin.on_command_result(mock_plugin_context, mock_inbox)

        assert result.transition == "conveyor_complete"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value
        assert result.wait is not None
        logger.info("✓ Step 3 完成: 流水线传输完成，生成出料命令")

        # Step 4: 模拟出料完成
        logger.info("Step 4: 模拟出料完成")
        mock_plugin_context.session.context_json = {
            **result.context_patch,
            "ng_reason": "OUTPUT",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "SUCCESS",
            "data": {"actual_source": "PIPELINE_OUT", "actual_target": "OUTPUT_PLATFORM"},
        }

        result = await plugin.on_command_result(mock_plugin_context, mock_inbox)

        assert result.transition == "output_handled"
        assert result.complete is True
        assert result.context_patch["stage"] == SmtClassifierStage.COMPLETED.value
        logger.info("✓ Step 4 完成: 出料完成，流程结束")

        logger.info("=" * 60)
        logger.info("完整 OK 流程测试通过!")
        logger.info("=" * 60)

    @pytest.mark.asyncio
    async def test_scan_ng_flow(
        self,
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试扫码 NG 流程

        流程:
        1. 扫码 NG 事件 -> 生成 PICK_AND_PUT 命令 (放入 NG 缓存位)
        2. 命令结果 SUCCESS -> 流程结束
        """
        logger.info("=" * 60)
        logger.info("开始测试: 扫码 NG 流程")
        logger.info("=" * 60)

        # Step 1: 扫码 NG 事件
        logger.info("Step 1: 发送扫码 NG 事件")
        mock_plugin_context.session.status = "NEW"
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "E2E-TEST-NG-001",
            "scan_result": "NG",
            "location_id": "LEFT_STATION_INPUT",
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "scan_ng"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value
        assert result.commands[0].parameters["source_type"] == "INPUT_PLATFORM"
        assert result.commands[0].parameters["target_type"] == "NG_PLATFORM"
        assert result.wait is not None
        logger.info("✓ Step 1 完成: 扫码 NG，生成 NG 放置命令")

        # Step 2: 模拟 NG 放置完成
        logger.info("Step 2: 模拟 NG 放置完成")
        mock_plugin_context.session.context_json = {
            "stage": SmtClassifierStage.WAITING_PICK_PLACE.value,
            "ng_reason": "SCAN_NG",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "SUCCESS",
            "data": {"actual_source": "INPUT_PLATFORM", "actual_target": "NG_PLATFORM"},
        }

        result = await plugin.on_command_result(mock_plugin_context, mock_inbox)

        assert result.transition == "ng_handled"
        assert result.complete is True
        assert result.context_patch["stage"] == SmtClassifierStage.COMPLETED.value
        assert result.context_patch["ng_handled"] is True
        logger.info("✓ Step 2 完成: NG 放置完成，流程结束")

        logger.info("=" * 60)
        logger.info("扫码 NG 流程测试通过!")
        logger.info("=" * 60)

    @pytest.mark.asyncio
    async def test_inspection_ng_flow(
        self,
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试检测 NG 流程

        流程:
        1. 扫码 OK 事件 -> 等待检测
        2. 检测 NG 事件 -> 生成 PICK_AND_PUT 命令 (放入 NG 缓存位)
        3. 命令结果 SUCCESS -> 流程结束
        """
        logger.info("=" * 60)
        logger.info("开始测试: 检测 NG 流程")
        logger.info("=" * 60)

        # Step 1: 扫码 OK 事件
        logger.info("Step 1: 发送扫码 OK 事件")
        mock_plugin_context.session.status = "NEW"
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.SCAN_COMPLETED.value,
            "barcode": "E2E-TEST-NG-002",
            "scan_result": "OK",
            "location_id": "LEFT_STATION_INPUT",
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "scan_ok"
        logger.info("✓ Step 1 完成: 扫码 OK")

        # Step 2: 检测 NG 事件
        logger.info("Step 2: 发送检测 NG 事件")
        mock_plugin_context.session.context_json = result.context_patch
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.INSPECTION_COMPLETED.value,
            "inspection_result": "NG",
            "location_id": "LEFT_STATION_DETECT",
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "inspection_ng"
        assert len(result.commands) == 1
        assert result.commands[0].action == SmtClassifierCommandType.PICK_AND_PUT.value
        assert result.commands[0].parameters["reason"] == "INSPECTION_NG"
        assert result.wait is not None
        logger.info("✓ Step 2 完成: 检测 NG，生成 NG 放置命令")

        # Step 3: 模拟 NG 放置完成
        logger.info("Step 3: 模拟 NG 放置完成")
        mock_plugin_context.session.context_json = {
            **result.context_patch,
            "ng_reason": "INSPECTION_NG",
        }
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "SUCCESS",
        }

        result = await plugin.on_command_result(mock_plugin_context, mock_inbox)

        assert result.transition == "ng_handled"
        assert result.complete is True
        logger.info("✓ Step 3 完成: NG 放置完成，流程结束")

        logger.info("=" * 60)
        logger.info("检测 NG 流程测试通过!")
        logger.info("=" * 60)


@pytest.mark.e2e
class TestSmtClassifierE2EErrorHandling(TestSmtClassifierE2EBase):
    """SMT 粗分机 E2E 错误处理测试"""

    @pytest.fixture
    def plugin(self) -> SmtClassifierPlugin:
        """创建插件实例"""
        return SmtClassifierPlugin()

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
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试急停处理"""
        logger.info("=" * 60)
        logger.info("开始测试: 急停处理")
        logger.info("=" * 60)

        mock_plugin_context.session.status = "RUNNING"
        mock_inbox.payload_json = {
            "event_type": SmtClassifierEventType.ESTOP_PRESSED.value,
            "device_id": "ARM01",
            "timestamp": 1711363200000,
        }

        result = await plugin.on_device_event(mock_plugin_context, mock_inbox)

        assert result.transition == "estop"
        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "ESTOP_PRESSED"
        assert result.context_patch["estop_pressed"] is True
        assert result.context_patch["stage"] == SmtClassifierStage.ERROR.value

        logger.info("✓ 急停处理测试通过!")

    @pytest.mark.asyncio
    async def test_timeout_handling(
        self,
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试超时处理"""
        logger.info("=" * 60)
        logger.info("开始测试: 超时处理")
        logger.info("=" * 60)

        mock_plugin_context.session.status = "WAITING_DEVICE_RESULT"
        mock_plugin_context.session.context_json = {
            "stage": SmtClassifierStage.WAITING_CONVEYOR.value,
            "retry_count": 0,
        }
        mock_inbox.kind = "TIMER_TIMEOUT"

        result = await plugin.on_timeout(mock_plugin_context, mock_inbox)

        assert result.transition == "timeout"
        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "TIMEOUT"
        assert result.context_patch["timeout_at_stage"] == SmtClassifierStage.WAITING_CONVEYOR.value

        logger.info("✓ 超时处理测试通过!")

    @pytest.mark.asyncio
    async def test_command_failure_with_retry(
        self,
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试命令失败与重试逻辑"""
        logger.info("=" * 60)
        logger.info("开始测试: 命令失败与重试")
        logger.info("=" * 60)

        # 第一次失败
        mock_plugin_context.session.context_json = {"retry_count": 0}
        mock_inbox.payload_json = {
            "command_type": SmtClassifierCommandType.PICK_AND_PUT.value,
            "result": "FAILED",
            "error_detail": {"code": "DEVICE_TIMEOUT", "message": "设备响应超时"},
        }

        result = await plugin.on_command_result(mock_plugin_context, mock_inbox)

        # 检查是否进入重试状态
        assert result.transition == "retry"
        assert result.context_patch["retry_count"] == 1

        logger.info("✓ 命令失败重试测试通过!")


@pytest.mark.e2e
class TestSmtClassifierE2EManualOperations(TestSmtClassifierE2EBase):
    """SMT 粗分机 E2E 人工操作测试"""

    @pytest.fixture
    def plugin(self) -> SmtClassifierPlugin:
        """创建插件实例"""
        return SmtClassifierPlugin()

    @pytest.fixture
    def mock_inbox(self) -> MagicMock:
        """创建模拟 Inbox"""
        inbox = MagicMock()
        inbox.id = 5001
        inbox.kind = "MANUAL_OPERATION"
        inbox.payload_json = {}
        return inbox

    @pytest.mark.asyncio
    async def test_manual_hold_and_resume(
        self,
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试人工暂停和恢复"""
        logger.info("=" * 60)
        logger.info("开始测试: 人工暂停和恢复")
        logger.info("=" * 60)

        # 人工暂停
        mock_plugin_context.session.status = "RUNNING"
        mock_inbox.payload_json = {
            "operation_type": "MANUAL_HOLD",
            "reason": "Equipment check",
        }

        result = await plugin.on_manual_operation(mock_plugin_context, mock_inbox)

        assert result.transition == "manual_hold"
        assert result.context_patch["manual_hold"] is True
        assert result.context_patch["hold_reason"] == "Equipment check"

        logger.info("✓ 人工暂停测试通过")

        # 人工恢复
        mock_inbox.payload_json = {"operation_type": "MANUAL_RESUME"}

        result = await plugin.on_manual_operation(mock_plugin_context, mock_inbox)

        assert result.transition == "manual_resume"
        assert result.context_patch["manual_hold"] is False

        logger.info("✓ 人工恢复测试通过")

    @pytest.mark.asyncio
    async def test_manual_cancel(
        self,
        plugin: SmtClassifierPlugin,
        mock_plugin_context: MagicMock,
        mock_inbox: MagicMock,
    ) -> None:
        """测试人工取消"""
        logger.info("=" * 60)
        logger.info("开始测试: 人工取消")
        logger.info("=" * 60)

        mock_plugin_context.session.status = "RUNNING"
        mock_inbox.payload_json = {
            "operation_type": "MANUAL_CANCEL",
            "reason": "Quality issue",
        }

        result = await plugin.on_manual_operation(mock_plugin_context, mock_inbox)

        assert result.transition == "manual_cancel"
        assert result.complete is True
        assert result.context_patch["cancelled"] is True
        assert result.context_patch["cancel_reason"] == "Quality issue"

        logger.info("✓ 人工取消测试通过!")


@pytest.mark.e2e
class TestSmtClassifierE2EMockInteractions(TestSmtClassifierE2EBase):
    """SMT 粗分机与 Mock 服务的交互测试"""

    @pytest.mark.asyncio
    async def test_pipeline_mock_health_check(
        self,
        pipeline_client: httpx.AsyncClient,
    ) -> None:
        """测试 Pipeline Mock 服务健康检查"""
        response = await pipeline_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "SMT 粗分机流水线 Mock 服务"
        assert data["status"] == "running"
        assert data["device_code"] == "PIPELINE01"

    @pytest.mark.asyncio
    async def test_arm01_mock_health_check(
        self,
        arm01_client: httpx.AsyncClient,
    ) -> None:
        """测试 ARM01 Mock 服务健康检查"""
        response = await arm01_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "进料机械臂" in data["device_name"]
        assert data["status"] == "running"
        assert data["device_code"] == "ARM01"

    @pytest.mark.asyncio
    async def test_arm02_mock_health_check(
        self,
        arm02_client: httpx.AsyncClient,
    ) -> None:
        """测试 ARM02 Mock 服务健康检查"""
        response = await arm02_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "出料机械臂" in data["device_name"]
        assert data["status"] == "running"
        assert data["device_code"] == "ARM02"

    @pytest.mark.asyncio
    async def test_pipeline_scan_event(
        self,
        arm01_client: httpx.AsyncClient,
    ) -> None:
        """测试 ARM01 扫码事件触发"""
        result = await self.trigger_pipeline_scan(
            arm01_client,
            barcode="E2E-SCAN-001",
            result="OK",
        )

        assert result["task_type"] == "SCAN_COMPLETED"
        assert result["result"] == "OK"
        assert result["reported_event_type"] == "SCAN_COMPLETED"
        assert result["source"]["location_id"] == "STATION_INPUT1"

    @pytest.mark.asyncio
    async def test_arm_manual_execution(
        self,
        arm01_client: httpx.AsyncClient,
    ) -> None:
        """测试机械臂手动执行"""
        response = await arm01_client.post(
            "/debug/execute",
            json={
                "task_type": "PICK_AND_PUT",
                "source_type": "INPUT_PLATFORM",
                "target_type": "PIPELINE_PLATFORM",
                "barcode": "E2E-ARM-001",
                "execution_time": 0.1,  # 快速执行
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["task_type"] == "PICK_AND_PUT"
        assert result["result"] == "SUCCESS"
        assert result["source"]["location_id"] == "STATION_INPUT1"
        assert result["target"]["location_id"] == "STATION_PIPELINE1_INPUT1"

    @pytest.mark.asyncio
    async def test_arm_device_status(self, arm01_client: httpx.AsyncClient) -> None:
        """测试机械臂设备状态查询"""
        result = await self.get_arm_status(arm01_client)

        assert result["device_code"] == "ARM01"
        assert result["status"] in ["IDLE", "RUNNING"]
        assert result["error_code"] == "NONE"
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_arm_command_ack(self, arm01_client: httpx.AsyncClient) -> None:
        """测试机械臂命令 ACK 响应"""
        import time

        payload = {
            "command_code": f"E2E-CMD-{int(time.time())}",
            "task_type": "PICK_AND_PUT",
            "priority": 1,
            "timeout": 30,
            "params": {"source_type": "INPUT_PLATFORM", "target_type": "PIPELINE_PLATFORM"},
            "timestamp": int(time.time()),
        }

        response = await arm01_client.post("/api/v1/device/command", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["code"] == 200
        assert result["message"] == "Accepted"
        assert "trace_id" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
