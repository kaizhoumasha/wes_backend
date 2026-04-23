"""SMT 分类插件事件入口测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier import SmtClassifierPlugin, smt_classifier_plugin
from src.workline_runtime.types import CommandTargetScope


class TestSmtClassifierPluginEvents:
    """SMT 分类插件事件入口测试。"""

    @pytest.mark.asyncio
    async def test_scan_completed_ok_flow(self, plugin, mock_context):
        """测试扫码 OK 流程会进入测量等待态。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "SVYU00125TP4LCR02_2",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ok"
        assert result.failure is None
        assert result.commands is not None
        assert len(result.commands) == 1
        assert result.commands[0].action == "MEASUREMENT_REEL"
        assert result.commands[0].target_scope == CommandTargetScope.CURRENT
        assert result.commands[0].device_role is None
        assert result.commands[0].parameters["pkg_id"] == "SVYU00125TP4LCR02_2"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-MEASUREMENT_REEL-")
        assert result.wait.deadline_seconds == 300
        assert result.context_patch["device_code"] == "SCANNER01"
        assert result.context_patch["location"] == "LOC01"
        assert result.context_patch["step_code"] == "WAITING_MEASUREMENT"
        assert len(result.context_patch["barcodes"]) == 6

    @pytest.mark.asyncio
    async def test_scan_completed_incomplete_barcodes_routes_to_scan_ng(self, plugin, mock_context):
        """测试条码不完整时会进入 scan_ng，并继续等待 NG 分流结果。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "LotCode": "LOTABC123",
                "DateCode": "20260409",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is None
        assert result.commands is not None
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.context_patch["pick_place_reason"] == "SCAN_NG"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_scan_completed_ng_flow(self, plugin, mock_context):
        """测试扫码 NG 流程，命中业务 NG 规则。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "LOTSIZENG_001",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is None
        assert result.commands is not None
        assert len(result.commands) == 1
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.commands[0].parameters["target_type"] == "NG_PLATFORM"
        assert result.context_patch["pick_place_reason"] == "SCAN_NG"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_scan_completed_requires_data(self, plugin, mock_context):
        """测试扫码事件缺少 data 时返回 MISSING_SCAN_DATA。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
        }

        inbox = MagicMock()
        inbox.id = 11
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "MISSING_SCAN_DATA"
        assert not result.commands

    @pytest.mark.asyncio
    async def test_scan_completed_rejects_flattened_business_fields(self, plugin, mock_context):
        """测试扫码事件业务字段必须放在 data 中，不能拍平到顶层。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "HHPN": "620100L00-011-G",
            "MfrPN": "CC0402JRNPO9BN220",
            "Qty": "7387",
            "DateCode": "122625",
            "LotCode": "8904936031",
            "PkgID": "SVYU00125TP4LCR02_2",
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 12
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "MISSING_SCAN_DATA"
        assert not result.commands

    @pytest.mark.asyncio
    async def test_scan_invalid_barcode(self, plugin, mock_context):
        """测试无效条码时会进入 scan_ng，并继续等待 NG 分流结果。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "X",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ng"
        assert result.failure is None
        assert result.commands is not None
        assert result.commands[0].action == "PICK_AND_PUT"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-PICK_AND_PUT-")
        assert result.context_patch["pick_place_reason"] == "SCAN_NG"
        assert result.context_patch["step_code"] == "WAITING_PICK_PLACE"

    @pytest.mark.asyncio
    async def test_scan_completed_rejects_legacy_device_id(self, plugin, mock_context):
        """测试扫码事件不再接受 legacy device_id。"""
        payload = {
            "device_id": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "LotCode": "LOTABC123",
            "location": "LOC01",
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "DATA"
        assert result.failure.code == "PAYLOAD_INVALID"

    @pytest.mark.asyncio
    async def test_scan_completed_accepts_canonical_event_type(self, plugin, mock_context):
        """测试粗分机插件可按标准化 canonical_event_type 路由扫码事件。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "VENDOR_SCAN_DONE",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "SVYU00125TP4LCR02_2",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 101
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "IDLE"}
        mock_context.normalized_input = MagicMock(canonical_event_type="SCAN_COMPLETED")

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ok"
        assert result.commands is not None
        assert result.commands[0].action == "MEASUREMENT_REEL"
        assert result.wait is not None
        assert result.wait.wait_type == "COMMAND_RESULT"
        assert result.wait.wait_token.startswith("42-MEASUREMENT_REEL-")

    @pytest.mark.asyncio
    async def test_estop_event_returns_hardware_failure(self, plugin, mock_context):
        """测试急停事件会直接落到硬件失败。"""
        payload = {
            "device_code": "ARM01",
            "event_type": "ESTOP_PRESSED",
            "data": None,
        }

        inbox = MagicMock()
        inbox.id = 102
        inbox.payload_json = payload
        mock_context.session.context_json = {"step_code": "WAITING_PICK_PLACE"}

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.failure is not None
        assert result.failure.domain == "HARDWARE"
        assert result.failure.code == "ESTOP"
        assert result.failure.message == "急停触发: ARM01"

    @pytest.mark.asyncio
    async def test_idle_to_waiting_measurement(self, plugin, mock_context):
        """测试 IDLE → WAITING_MEASUREMENT 迁移。"""
        mock_context.session.context_json = {"step_code": "IDLE"}

        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "SVYU00125TP4LCR02_2",
                "location": "LOC01",
            },
        }

        inbox = MagicMock()
        inbox.id = 1
        inbox.payload_json = payload

        result = await plugin.on_device_event(mock_context, inbox)

        assert result.transition == "scan_ok"
        assert result.context_patch.get("step_code") == "WAITING_MEASUREMENT"


class TestSmtClassifierPluginBasics:
    """插件注册测试。"""

    def test_plugin_key(self):
        """验证 plugin_key。"""
        assert SmtClassifierPlugin.plugin_key == "smt_classifier"

    def test_contract_version(self):
        """验证 contract_version。"""
        assert SmtClassifierPlugin.contract_version == "1.0"

    def test_plugin_instance(self):
        """验证插件实例可创建。"""
        assert smt_classifier_plugin is not None
        assert isinstance(smt_classifier_plugin, SmtClassifierPlugin)
