"""SMT 分类插件事件入口 RuntimeIntent 测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier import SmtClassifierPlugin, smt_classifier_plugin
from src.workline_runtime.runtime_intent import BlockScope, DestinationKind, RuntimeIntentKind


def _make_inbox(payload: dict) -> MagicMock:
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = payload
    inbox.kind = None
    inbox.trace_id = "trace-smt-events"
    return inbox


def _scan_payload(pkg_id: str = "SVYU00125TP4LCR02_2") -> dict:
    return {
        "device_code": "SCANNER01",
        "event_type": "SCAN_COMPLETED",
        "data": {
            "HHPN": "620100L00-011-G",
            "MfrPN": "CC0402JRNPO9BN220",
            "Qty": "7387",
            "DateCode": "122625",
            "LotCode": "8904936031",
            "PkgID": pkg_id,
            "location": "LOC01",
        },
    }


def _assert_command(intent, *, action: str, device_role: str, timeout: int = 300) -> None:
    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.action == action
    assert intent.device_role == device_role
    assert intent.destination.kind == DestinationKind.ROLE
    assert intent.destination.value == device_role
    assert intent.timeout_seconds == timeout


class TestSmtClassifierPluginEvents:
    """SMT 分类插件事件入口 RuntimeIntent 测试。"""

    @pytest.mark.asyncio
    async def test_scan_completed_ok_updates_context_and_measures(self, plugin, mock_context):
        """扫码 OK 后写入上下文，并下发测量命令到进料臂。"""

        result = await plugin.on_device_event(mock_context, _make_inbox(_scan_payload()))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
        assert result[0].context_patch["device_code"] == "SCANNER01"
        assert result[0].context_patch["location"] == "LOC01"
        assert result[0].context_patch["barcode"] == "SVYU00125TP4LCR02_2"
        assert len(result[0].context_patch["barcodes"]) == 6
        _assert_command(result[1], action="MEASUREMENT_REEL", device_role="INPUT_ARM")
        assert result[1].payload_json == {"pkg_id": "SVYU00125TP4LCR02_2"}

    @pytest.mark.asyncio
    async def test_scan_completed_incomplete_barcodes_marks_ng_and_picks_to_ng(self, plugin, mock_context):
        """条码不完整时标记 NG，写入 SCAN_NG 上下文，并下发 NG 分流。"""
        payload = {
            "device_code": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "LotCode": "LOTABC123",
                "DateCode": "20260409",
                "location": "LOC01",
            },
        }

        result = await plugin.on_device_event(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.MARK_NG,
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.COMMAND,
        ]
        assert result[0].reason_code == "BARCODE_INCOMPLETE"
        assert result[0].payload_json["barcode"] == ""
        assert result[1].context_patch["pick_place_reason"] == "SCAN_NG"
        assert result[1].context_patch["scan_ng_reason_code"] == "BARCODE_INCOMPLETE"
        _assert_command(result[2], action="PICK_AND_PUT", device_role="INPUT_ARM")
        assert result[2].payload_json == {
            "barcode": "",
            "source_type": "INPUT_PLATFORM",
            "target_type": "NG_PLATFORM",
        }

    @pytest.mark.asyncio
    async def test_scan_completed_ng_rule_marks_ng_and_picks_to_ng(self, plugin, mock_context):
        """业务规则判定 NG 时标记 SCAN_NG，并下发 NG 分流。"""

        result = await plugin.on_device_event(mock_context, _make_inbox(_scan_payload("LOTSIZENG_001")))

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.MARK_NG,
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.COMMAND,
        ]
        assert result[0].reason_code == "SCAN_NG"
        assert result[0].message == "扫码判定 NG"
        assert result[0].payload_json["barcode"] == "LOTSIZENG_001"
        assert result[0].payload_json["device_code"] == "SCANNER01"
        assert result[1].context_patch["barcode"] == "LOTSIZENG_001"
        assert result[1].context_patch["pick_place_reason"] == "SCAN_NG"
        _assert_command(result[2], action="PICK_AND_PUT", device_role="INPUT_ARM")
        assert result[2].payload_json["target_type"] == "NG_PLATFORM"

    @pytest.mark.asyncio
    async def test_scan_completed_without_data_blocks_material(self, plugin, mock_context):
        """扫码事件缺少 data 时返回 MISSING_SCAN_DATA BLOCK。"""
        payload = {"device_code": "SCANNER01", "event_type": "SCAN_COMPLETED"}

        result = await plugin.on_device_event(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "MISSING_SCAN_DATA"
        assert result[0].message == "扫码事件缺少 data 字段"

    @pytest.mark.asyncio
    async def test_scan_completed_rejects_flattened_business_fields(self, plugin, mock_context):
        """扫码事件业务字段必须放在 data 中，不能拍平到顶层。"""
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

        result = await plugin.on_device_event(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "MISSING_SCAN_DATA"

    @pytest.mark.asyncio
    async def test_scan_completed_rejects_invalid_payload_as_block(self, plugin, mock_context):
        """扫码事件 payload 包络非法时返回 PAYLOAD_INVALID BLOCK。"""
        payload = {
            "device_id": "SCANNER01",
            "event_type": "SCAN_COMPLETED",
            "data": {"LotCode": "LOTABC123", "location": "LOC01"},
        }

        result = await plugin.on_device_event(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "PAYLOAD_INVALID"

    @pytest.mark.asyncio
    async def test_scan_completed_accepts_canonical_event_type(self, plugin, mock_context):
        """标准化 canonical_event_type 可路由到扫码 handler。"""
        payload = _scan_payload()
        payload["event_type"] = "VENDOR_SCAN_DONE"
        mock_context.normalized_input = MagicMock(canonical_event_type="SCAN_COMPLETED")

        result = await plugin.on_device_event(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
        _assert_command(result[1], action="MEASUREMENT_REEL", device_role="INPUT_ARM")


class TestSmtClassifierPluginBasics:
    """插件注册测试。"""

    def test_plugin_key(self):
        assert SmtClassifierPlugin.plugin_key == "smt_classifier"

    def test_contract_version(self):
        assert SmtClassifierPlugin.contract_version == "1.0"

    def test_manifest_does_not_export_state_machine(self):
        assert not hasattr(SmtClassifierPlugin.manifest, "state" + "_machine_class")

    def test_plugin_instance(self):
        assert smt_classifier_plugin is not None
        assert isinstance(smt_classifier_plugin, SmtClassifierPlugin)
