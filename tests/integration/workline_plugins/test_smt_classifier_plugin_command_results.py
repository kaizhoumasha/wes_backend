"""SMT 分类插件命令结果 RuntimeIntent 测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.app.workline.domain.services import (
    SmtFullBoxExchangeRequest,
    SmtRackBinSchedulingDecision,
    SmtRackBinSchedulingService,
)
from src.workline_plugins.smt_classifier.contract import build_output_to_bin_params
from src.workline_runtime.runtime_intent import BlockScope, DestinationKind, RuntimeIntentKind
from src.workline_runtime.services import WorklineRuntimeServices


def _make_inbox(payload: dict) -> MagicMock:
    inbox = MagicMock()
    inbox.id = 1
    inbox.payload_json = payload
    inbox.kind = None
    inbox.trace_id = "trace-smt-command"
    return inbox


def _command_payload(command_type: str, result: str, *, data: dict | None = None, error_detail: dict | None = None):
    payload = {
        "command_code": f"CMD-{command_type}-{result}",
        "command_type": command_type,
        "result": result,
        "device_code": "DEVICE01",
    }
    if data is not None:
        payload["data"] = data
    if error_detail is not None:
        payload["error_detail"] = error_detail
    return payload


def _wms_callback_payload(**overrides: object) -> dict:
    payload = {
        "callback_type": "WMS_RACK_EXCHANGE_PROGRESS",
        "trace_id": "trace-smt-command",
        "dispatch_key": "external:smt_classifier:trace-smt-command:RACK_SUPPLY",
        "source_system": "WMS",
        "source_event_id": "wms-event-001",
        "source_version": "1",
        "occurred_at": "2026-05-16T08:00:00Z",
        "request_id": "REQ-WMS-001",
        "timestamp": "2026-05-16T08:00:01Z",
        "signature": "test-signature",
    }
    payload.update(overrides)
    return payload


def _assert_command(intent, *, action: str, device_role: str, timeout: int = 300) -> None:
    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.action == action
    assert intent.device_role == device_role
    assert intent.destination.kind == DestinationKind.ROLE
    assert intent.destination.value == device_role
    assert intent.timeout_seconds == timeout


def _assert_bin_cell_reservation(intent, *, pkg_code: str, bin_code: str, bin_cell_index: str) -> None:
    assert intent.kind == RuntimeIntentKind.RESOURCE_RESERVATION
    assert intent.action == "CLAIM_BIN_CELL"
    assert intent.payload_json["pkg_code"] == pkg_code
    assert intent.payload_json["bin_code"] == bin_code
    assert intent.payload_json["bin_cell_index"] == bin_cell_index


def _assert_bin_cell_consumption(intent, *, bin_code: str, bin_cell_index: str) -> None:
    assert intent.kind == RuntimeIntentKind.RESOURCE_RESERVATION
    assert intent.action == "CONSUME_BIN_CELL"
    assert intent.payload_json["bin_code"] == bin_code
    assert intent.payload_json["bin_cell_index"] == bin_cell_index


def _assert_material_mounted_fact(intent, *, pkg_code: str, bin_code: str, bin_cell_index: str) -> None:
    assert intent.kind == RuntimeIntentKind.RESOURCE_FACT
    assert intent.action == "MATERIAL_MOUNTED"
    assert intent.payload_json["pkg_code"] == pkg_code
    assert intent.payload_json["bin_code"] == bin_code
    assert intent.payload_json["bin_cell_index"] == bin_cell_index


class TestSmtClassifierPluginCommandResults:
    """SMT 分类插件命令结果 RuntimeIntent 测试。"""

    def test_build_output_to_bin_params_includes_bin_cell_location(self):
        """出料命令参数必须携带具体料箱格。"""

        params = build_output_to_bin_params(
            pkg_id="CALLBACK-PKG-001",
            reel_diameter="178.5",
            bin_location={
                "rack_id": "NHW-1CLJ-0096",
                "rack_slot_code": "C",
                "rack_slot_location_code": "NHW-1CLJ-0096-1C-1",
                "bin_id": "BIN-001",
                "bin_orientation_code": "BIN-001-A",
                "bin_type": "6格箱",
                "bin_cell_location": "BIN-001-6",
                "bin_cell_index": "6",
            },
        )

        assert params == {
            "barcode": "CALLBACK-PKG-001",
            "reel_diameter": "178.5",
            "target_type": "BIN",
            "target_loc": "BIN-001",
            "rack_id": "NHW-1CLJ-0096",
            "rack_slot_code": "C",
            "rack_slot_location_code": "NHW-1CLJ-0096-1C-1",
            "bin_id": "BIN-001",
            "bin_orientation_code": "BIN-001-A",
            "bin_type": "6格箱",
            "bin_cell_location": "BIN-001-6",
            "bin_cell_index": "6",
        }

    @pytest.mark.asyncio
    async def test_pick_success_from_input_arm_completes_scan_ng_flow(self, plugin, mock_context):
        """进料臂 SCAN_NG 分流成功后先写 ng_handled，再完成。"""
        mock_context.source_device_role = "INPUT_ARM"
        mock_context.session.context_json = {
            "barcode": "LOTSIZENG",
            "pick_place_reason": "SCAN_NG",
        }

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("PICK_AND_PUT", "SUCCESS")),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMPLETE]
        assert result[0].context_patch == {"ng_handled": True}
        assert result[-1].kind == RuntimeIntentKind.COMPLETE

    @pytest.mark.asyncio
    async def test_pick_success_from_input_arm_completes_inspection_ng_flow(self, plugin, mock_context):
        """进料臂检测 NG 回送成功后先写 ng_handled，再完成，不再前进流水线。"""
        mock_context.source_device_role = "INPUT_ARM"
        mock_context.session.context_json = {
            "barcode": "LOTABC123",
            "inspection_error": "INSPECTION_SIZE_NG",
        }

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("PICK_AND_PUT", "SUCCESS")),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMPLETE]
        assert result[0].context_patch == {"ng_handled": True}
        assert result[-1].kind == RuntimeIntentKind.COMPLETE

    @pytest.mark.asyncio
    async def test_pick_success_from_input_arm_moves_forward_for_ok_material(self, plugin, mock_context):
        """进料臂普通抓取成功后写测量字段，并下发流水线前进。"""
        mock_context.source_device_role = "INPUT_ARM"
        mock_context.session.context_json = {"barcode": "LOTABC123"}

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "PICK_AND_PUT",
                    "SUCCESS",
                    data={"reel_diameter": "178.5", "reel_thickness": "12.3"},
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
        assert result[0].context_patch == {"reel_diameter": "178.5", "reel_thickness": "12.3"}
        _assert_command(result[1], action="MOVE_FORWARD", device_role="CONVEYOR")
        assert result[1].payload_json == {"pkg_id": "LOTABC123"}

    @pytest.mark.asyncio
    async def test_pick_success_from_output_arm_completes(self, plugin, mock_context):
        """出料臂抓取成功后先写物料占格事实，再完成。"""
        mock_context.source_device_role = "OUTPUT_ARM"
        mock_context.session.context_json = {
            "pkg_id": "PKG-OUTPUT-001",
            "six_in_one": {
                "HHPN": "620100L00-011-G",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "PKG-OUTPUT-001",
            },
            "bin_location": {
                "bin_id": "BIN-001",
                "bin_cell_location": "BIN-001-4",
                "bin_cell_index": "4",
            },
        }

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("PICK_AND_PUT", "SUCCESS")),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.RESOURCE_FACT,
            RuntimeIntentKind.RESOURCE_RESERVATION,
            RuntimeIntentKind.COMPLETE,
        ]
        _assert_material_mounted_fact(result[0], pkg_code="PKG-OUTPUT-001", bin_code="BIN-001", bin_cell_index="4")
        _assert_bin_cell_consumption(result[1], bin_code="BIN-001", bin_cell_index="4")
        assert result[0].payload_json["material_identity_key"] == "MAT:620100L00-011-G:122625:8904936031"

    @pytest.mark.asyncio
    async def test_pick_success_from_unexpected_role_blocks_material(self, plugin, mock_context):
        """非进料臂/出料臂上报 PICK_AND_PUT SUCCESS 时阻塞当前物料。"""
        mock_context.source_device_role = "CONVEYOR"

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("PICK_AND_PUT", "SUCCESS")),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "UNEXPECTED_DEVICE_ROLE"

    @pytest.mark.asyncio
    async def test_measurement_reel_success_updates_context_and_moves_forward(self, plugin, mock_context):
        """测量成功会写入测量上下文并下发流水线传输。"""

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "MEASUREMENT_REEL",
                    "SUCCESS",
                    data={
                        "pkg_id": "SVYU00125TP4LCR02_2",
                        "reel_diameter": 178.5,
                        "reel_thickness": 12.3,
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
        assert result[0].context_patch == {
            "pkg_id": "SVYU00125TP4LCR02_2",
            "reel_diameter": 178.5,
            "reel_thickness": 12.3,
        }
        _assert_command(result[1], action="MOVE_FORWARD", device_role="CONVEYOR")
        assert result[1].payload_json == {"pkg_id": "SVYU00125TP4LCR02_2"}

    @pytest.mark.asyncio
    async def test_measurement_reel_success_preserves_existing_six_in_one_context(self, plugin, mock_context):
        """测量成功只补测量字段，不覆盖扫码阶段保存的 6 合 1。"""
        mock_context.session.context_json = {
            "six_in_one": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": "SVYU00125TP4LCR02_2",
            }
        }

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "MEASUREMENT_REEL",
                    "SUCCESS",
                    data={
                        "pkg_id": "SVYU00125TP4LCR02_2",
                        "reel_diameter": 178.5,
                        "reel_thickness": 12.3,
                    },
                )
            ),
        )

        assert "six_in_one" not in result[0].context_patch
        assert result[0].context_patch["reel_diameter"] == 178.5
        assert result[0].context_patch["reel_thickness"] == 12.3

    @pytest.mark.asyncio
    async def test_measurement_reel_success_with_inspection_ng_marks_ng_and_picks_to_ng(self, plugin, mock_context):
        """检测 NG 是业务结果：设备动作成功，插件标记 NG 并下发 NG 分流。"""

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "MEASUREMENT_REEL",
                    "SUCCESS",
                    data={
                        "pkg_id": "PKG-SIZE-NG",
                        "inspection_result": "NG",
                        "reason_code": "INSPECTION_SIZE_NG",
                        "reason_message": "料盘尺寸检测 NG",
                        "reel_diameter": 178.5,
                        "reel_thickness": 12.3,
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.MARK_NG,
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.COMMAND,
        ]
        assert result[0].reason_code == "INSPECTION_SIZE_NG"
        assert result[0].message == "料盘尺寸检测 NG"
        assert result[0].payload_json["barcode"] == "PKG-SIZE-NG"
        assert result[1].context_patch == {
            "pkg_id": "PKG-SIZE-NG",
            "reel_diameter": 178.5,
            "reel_thickness": 12.3,
            "inspection_error": "INSPECTION_SIZE_NG",
        }
        _assert_command(result[2], action="PICK_AND_PUT", device_role="INPUT_ARM")
        assert result[2].payload_json == {
            "barcode": "PKG-SIZE-NG",
            "source_type": "PIPELINE_PLATFORM",
            "target_type": "NG_PLATFORM",
        }

    @pytest.mark.asyncio
    async def test_measurement_reel_success_requires_data(self, plugin, mock_context):
        """测量成功缺少 data 时返回 PAYLOAD_INVALID BLOCK。"""

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MEASUREMENT_REEL", "SUCCESS")),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "PAYLOAD_INVALID"
        assert result[0].message == "测量成功回调缺少 data 字段"

    @pytest.mark.asyncio
    async def test_measurement_reel_success_requires_pkg_id(self, plugin, mock_context):
        """测量成功缺少 PkgID/pkg_id 时返回 PAYLOAD_INVALID BLOCK。"""

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "MEASUREMENT_REEL",
                    "SUCCESS",
                    data={"reel_diameter": 178.5, "reel_thickness": 12.3},
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].reason_code == "PAYLOAD_INVALID"
        assert result[0].message == "测量成功回调缺少 PkgID/pkg_id"

    @pytest.mark.asyncio
    async def test_pick_failed_from_input_arm_dimension_ng_code_blocks_as_invalid_device_failure(
        self, plugin, mock_context
    ):
        """检测 NG 不允许再通过 PICK_AND_PUT FAILED/error_detail 表达。"""
        mock_context.source_device_role = "INPUT_ARM"
        mock_context.session.context_json = {"barcode": "LOTABC123"}

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "PICK_AND_PUT",
                    "FAILED",
                    error_detail={
                        "error_code": "INSPECTION_SIZE_NG",
                        "error_message": "料盘尺寸检测异常",
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.COMMAND
        assert result[0].reason_code == "INSPECTION_SIZE_NG"

    @pytest.mark.asyncio
    async def test_pick_failed_from_input_arm_manual_hold_updates_context_then_blocks(self, plugin, mock_context):
        """进料臂人工介入类错误先写上下文，再以 MATERIAL BLOCK 终止。"""
        mock_context.source_device_role = "INPUT_ARM"

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "PICK_AND_PUT",
                    "FAILED",
                    error_detail={
                        "error_code": "PICK_AND_PUT_FAILED",
                        "error_message": "机械臂搬运失败",
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.BLOCK]
        assert result[0].context_patch["manual_hold"] is True
        assert result[0].context_patch["manual_hold_reason_code"] == "PICK_AND_PUT_FAILED"
        assert result[1].block_scope == BlockScope.MATERIAL
        assert result[1].reason_code == "PICK_AND_PUT_FAILED"
        assert result[-1].kind == RuntimeIntentKind.BLOCK

    @pytest.mark.asyncio
    async def test_pick_failed_from_input_arm_unknown_blocks_command(self, plugin, mock_context):
        """进料臂未知 PICK_AND_PUT 失败阻塞当前命令。"""
        mock_context.source_device_role = "INPUT_ARM"

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "PICK_AND_PUT",
                    "FAILED",
                    error_detail={"error_code": "ARM_ERROR", "error_message": "机械臂错误"},
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.COMMAND
        assert result[0].reason_code == "ARM_ERROR"
        assert result[0].message == "抓取放置失败: 机械臂错误"

    @pytest.mark.asyncio
    async def test_pick_failed_from_output_arm_manual_hold_updates_context_then_blocks(self, plugin, mock_context):
        """出料臂人工介入类错误先写上下文，再以 MATERIAL BLOCK 终止。"""
        mock_context.source_device_role = "OUTPUT_ARM"

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "PICK_AND_PUT",
                    "FAILED",
                    error_detail={"error_code": "BIN_FULL", "error_message": "料箱已满"},
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.BLOCK]
        assert result[0].context_patch["manual_hold"] is True
        assert result[1].block_scope == BlockScope.MATERIAL
        assert result[1].reason_code == "BIN_FULL"
        assert result[1].message == "料箱已满"

    @pytest.mark.asyncio
    async def test_pick_failed_from_output_arm_unknown_blocks_command(self, plugin, mock_context):
        """出料臂未知失败按 COMMAND BLOCK 返回原始错误信息。"""
        mock_context.source_device_role = "OUTPUT_ARM"

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "PICK_AND_PUT",
                    "FAILED",
                    error_detail={"error_code": "OUTPUT_ERROR", "error_message": "出料异常"},
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.COMMAND
        assert result[0].reason_code == "OUTPUT_ERROR"
        assert result[0].message == "出料异常"

    @pytest.mark.asyncio
    async def test_pick_failed_accepts_normalized_failure_alias(self, plugin, mock_context):
        """标准化失败语义仍应路由到 PICK_AND_PUT FAILED handler。"""
        mock_context.source_device_role = "INPUT_ARM"
        mock_context.normalized_input = MagicMock(
            command_type="PICK_AND_PUT",
            source_result="ERROR",
            normalized_result="TERMINAL_FAILURE",
        )

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                {
                    "command_code": "CMD-001",
                    "command_type": "PICK_AND_PUT",
                    "result": "ERROR",
                    "device_code": "ARM01",
                    "error_detail": {"error_code": "ARM_ERROR", "error_message": "机械臂错误"},
                }
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].reason_code == "ARM_ERROR"

    @pytest.mark.asyncio
    async def test_conveyor_success_updates_bin_context_and_commands_output_arm(self, plugin, mock_context):
        """流水线成功后写入 pkg_id/bin_location，并下发出料臂命令。"""
        mock_context.session.context_json = {"reel_diameter": "178.5"}

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-001"})),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.RESOURCE_RESERVATION,
            RuntimeIntentKind.COMMAND,
        ]
        assert result[0].context_patch["pkg_id"] == "CALLBACK-PKG-001"
        assert "bin_location" in result[0].context_patch
        _assert_bin_cell_reservation(
            result[1],
            pkg_code="CALLBACK-PKG-001",
            bin_code=result[0].context_patch["bin_location"]["bin_id"],
            bin_cell_index=result[0].context_patch["bin_location"]["bin_cell_index"],
        )
        _assert_command(result[2], action="PICK_AND_PUT", device_role="OUTPUT_ARM")
        assert result[2].payload_json["barcode"] == "CALLBACK-PKG-001"
        assert result[2].payload_json["reel_diameter"] == "178.5"
        assert result[2].payload_json["bin_id"] == result[0].context_patch["bin_location"]["bin_id"]
        assert (
            result[2].payload_json["bin_cell_location"] == result[0].context_patch["bin_location"]["bin_cell_location"]
        )
        assert result[0].context_patch["bin_location"] == SmtRackBinSchedulingService().allocate("CALLBACK-PKG-001")

    @pytest.mark.asyncio
    async def test_conveyor_success_uses_bin_allocator_service(self, plugin, mock_context):
        """料箱分配优先走 ctx.services 内部领域服务。"""

        class BinAllocator:
            def allocate(self, barcode: str) -> dict:
                assert barcode == "CALLBACK-PKG-002"
                return {
                    "rack_id": "NHW-1CLJ-0001",
                    "rack_slot_code": "A",
                    "rack_slot_location_code": "NHW-1CLJ-0001-1A-0",
                    "bin_id": "BIN-SVC-001",
                    "bin_orientation_code": "BIN-SVC-001-A",
                    "bin_type": "6格箱",
                    "bin_cell_location": "6",
                }

        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())
        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-002"})),
        )

        assert result[0].context_patch["bin_location"]["bin_id"] == "BIN-SVC-001"
        _assert_bin_cell_reservation(result[1], pkg_code="CALLBACK-PKG-002", bin_code="BIN-SVC-001", bin_cell_index="6")
        assert result[2].payload_json["target_loc"] == "BIN-SVC-001"
        assert result[2].payload_json["rack_slot_code"] == "A"
        assert result[2].payload_json["rack_slot_location_code"] == "NHW-1CLJ-0001-1A-0"
        assert result[2].payload_json["bin_id"] == "BIN-SVC-001"
        assert result[2].payload_json["bin_type"] == "6格箱"
        assert result[2].payload_json["bin_cell_location"] == "BIN-SVC-001-6"
        assert result[2].payload_json["bin_cell_index"] == "6"

    @pytest.mark.asyncio
    async def test_conveyor_success_blocks_allocator_result_when_bin_cell_mismatches_reel_size(
        self, plugin, mock_context
    ):
        """即使 allocator 返回了位置，插件也要阻断与料盘尺寸不匹配的料格。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> SmtRackBinSchedulingDecision:
                assert barcode == "CALLBACK-PKG-007"
                assert context["reel_diameter"] == "15inch"
                return SmtRackBinSchedulingDecision(
                    bin_location={
                        "rack_id": "NHW-1CLJ-0001",
                        "rack_slot_code": "A",
                        "rack_slot_location_code": "NHW-1CLJ-0001-1A-0",
                        "bin_id": "BIN-SVC-LARGE",
                        "bin_orientation_code": "BIN-SVC-LARGE-A",
                        "bin_type": "6格箱",
                        "bin_cell_location": "BIN-SVC-LARGE-1",
                        "bin_cell_index": "1",
                    }
                )

        mock_context.session.context_json = {"reel_diameter": "15inch"}
        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-007"})),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].reason_code == "PAYLOAD_INVALID"
        assert result[0].message == "料箱调度结果与料盘尺寸不匹配"

    @pytest.mark.parametrize("missing_field", ["bin_id", "bin_type", "bin_cell_location"])
    @pytest.mark.asyncio
    async def test_conveyor_success_blocks_when_allocation_missing_required_bin_field(
        self, plugin, mock_context, missing_field
    ):
        """料箱调度结果缺少必填料箱字段时，不创建出料臂命令。"""

        class BinAllocator:
            def allocate(self, barcode: str) -> dict:
                assert barcode == "CALLBACK-PKG-MISSING-BIN-FIELD"
                allocation = {
                    "rack_id": "NHW-1CLJ-0001",
                    "rack_slot_code": "A",
                    "rack_slot_location_code": "NHW-1CLJ-0001-1A-0",
                    "bin_id": "BIN-SVC-003",
                    "bin_orientation_code": "BIN-SVC-003-A",
                    "bin_type": "6格箱",
                    "bin_cell_location": "6",
                }
                allocation.pop(missing_field)
                return allocation

        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())
        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-MISSING-BIN-FIELD"})),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "PAYLOAD_INVALID"
        assert result[0].message == f"料箱调度结果缺少 {missing_field}"

    @pytest.mark.asyncio
    async def test_conveyor_success_passes_six_in_one_to_allocator(self, plugin, mock_context):
        """流水线成功调度料箱时，将完整 session context 传给 allocator。"""
        six_in_one = {
            "HHPN": "620100L00-011-G",
            "MfrPN": "CC0402JRNPO9BN220",
            "Qty": "7387",
            "DateCode": "122625",
            "LotCode": "8904936031",
            "PkgID": "SVYU00125TP4LCR02_2",
        }

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> SmtRackBinSchedulingDecision:
                assert barcode == "SVYU00125TP4LCR02_2"
                assert context["six_in_one"] == six_in_one
                return SmtRackBinSchedulingDecision(
                    bin_location={
                        "rack_id": "NHW-1CLJ-0002",
                        "rack_slot_code": "B",
                        "rack_slot_location_code": "NHW-1CLJ-0002-1B-0",
                        "bin_id": "BIN-SVC-002",
                        "bin_orientation_code": "BIN-SVC-002-A",
                        "bin_type": "6格箱",
                        "bin_cell_location": "BIN-SVC-002-2",
                        "bin_cell_index": "2",
                    }
                )

        mock_context.session.context_json = {
            "six_in_one": six_in_one,
            "reel_diameter": "178.5",
        }
        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "SVYU00125TP4LCR02_2"})),
        )

        assert result[0].context_patch["bin_location"]["bin_id"] == "BIN-SVC-002"

    @pytest.mark.asyncio
    async def test_conveyor_success_passes_trace_and_session_token_to_allocator(self, plugin, mock_context):
        """调度料箱时，将真实 trace/session token 合并到 allocator context。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> SmtRackBinSchedulingDecision:
                assert barcode == "CALLBACK-PKG-TRACE"
                assert context["trace_id"] == "trace-smt-001"
                assert context["session_id"] == "session-smt-001"
                return SmtRackBinSchedulingDecision(
                    bin_location={
                        "rack_id": "NHW-1CLJ-0003",
                        "rack_slot_code": "D",
                        "rack_slot_location_code": "NHW-1CLJ-0003-1D-1",
                        "bin_id": "BIN-SVC-TRACE",
                        "bin_orientation_code": "BIN-SVC-TRACE-A",
                        "bin_type": "6格箱",
                        "bin_cell_location": "BIN-SVC-TRACE-6",
                        "bin_cell_index": "6",
                    }
                )

        mock_context.trace_id = "trace-smt-001"
        mock_context.session.id = "session-smt-001"
        mock_context.session.context_json = {"reel_diameter": "178.5"}
        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-TRACE"})),
        )

        assert result[0].context_patch["bin_location"]["bin_id"] == "BIN-SVC-TRACE"

    @pytest.mark.asyncio
    async def test_conveyor_success_without_active_rack_requests_supply_only(self, plugin, mock_context):
        """初次无货架时，SMT 只请求新货架补充，不触发满箱交换事件。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> SmtRackBinSchedulingDecision:
                assert barcode == "CALLBACK-PKG-003"
                assert context["reel_diameter"] == "178.5"
                return SmtRackBinSchedulingDecision(
                    rack_supply_request=SmtFullBoxExchangeRequest(
                        dispatch_key="external:smt_classifier:trace-001:RACK_SUPPLY",
                        target_code="http://wms-rcs/api/rack-supply",
                        payload={
                            "request_type": "SMT_RACK_SUPPLY",
                            "dispatch_key": "external:smt_classifier:trace-001:RACK_SUPPLY",
                            "actions": ["SUPPLY_EMPTY_RACK"],
                            "pkg_id": barcode,
                        },
                        timeout_seconds=1800,
                        source_system="WMS_RCS",
                    ),
                    reason_code="NO_ACTIVE_RACK",
                )

            def allocate(self, barcode: str) -> dict:
                raise AssertionError(f"不应在 plan_allocation 已返回交换决策后调用 allocate: {barcode}")

        mock_context.session.context_json = {"reel_diameter": "178.5"}
        mock_context.source_device_role = "CONVEYOR"
        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        payload = _command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-003"})
        payload["device_code"] = "PIPELINE02"
        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(payload),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.EXTERNAL_REQUEST,
        ]
        assert result[0].context_patch["pkg_id"] == "CALLBACK-PKG-003"
        assert result[0].context_patch["rack_supply"] == {
            "status": "REQUESTED",
            "dispatch_key": "external:smt_classifier:trace-001:RACK_SUPPLY",
            "target_code": "http://wms-rcs/api/rack-supply",
            "source_system": "WMS_RCS",
            "reason_code": "NO_ACTIVE_RACK",
            "requested_actions": ["SUPPLY_EMPTY_RACK"],
            "pkg_id": "CALLBACK-PKG-003",
            "resume_source_device_code": "PIPELINE02",
            "resume_source_device_role": "CONVEYOR",
            "resume_callback_type": "WMS_RACK_ARRIVED",
        }
        assert "rack_release_event" not in result[0].context_patch
        assert "full_box_exchange" not in result[0].context_patch
        assert "rack_exchange" not in result[0].context_patch
        assert result[1].dispatch_key == "external:smt_classifier:trace-001:RACK_SUPPLY"
        assert result[1].target_code == "http://wms-rcs/api/rack-supply"
        assert result[1].payload_json["request_type"] == "SMT_RACK_SUPPLY"
        assert result[1].timeout_seconds == 1800

    @pytest.mark.asyncio
    async def test_conveyor_success_requests_supply_from_mapping_decision(self, plugin, mock_context):
        """mapping 形式的新补架决策也走补架请求分支。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> dict:
                assert barcode == "CALLBACK-PKG-005"
                return {
                    "kind": "RACK_SUPPLY_REQUIRED",
                    "external_request": {
                        "dispatch_key": "external:smt_classifier:trace-005:RACK_SUPPLY",
                        "target_code": "http://wms-rcs/api/rack-supply",
                        "payload": {
                            "request_type": "SMT_RACK_SUPPLY",
                            "actions": ["SUPPLY_EMPTY_RACK"],
                            "pkg_id": barcode,
                        },
                        "timeout_seconds": 1800,
                        "source_system": "WMS_RCS",
                    },
                }

        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-005"})),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.EXTERNAL_REQUEST,
        ]
        assert result[0].context_patch["rack_supply"]["dispatch_key"] == "external:smt_classifier:trace-005:RACK_SUPPLY"
        assert "full_box_exchange" not in result[0].context_patch
        assert result[1].target_code == "http://wms-rcs/api/rack-supply"

    @pytest.mark.asyncio
    async def test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store(
        self, plugin, mock_context
    ):
        """当前货架快照不完整时阻断对账，不继续补架。"""

        pkg_id = "SVYU00125TP4LCR02_2"
        mock_context.trace_id = "trace-rack-full-001"
        mock_context.source_device_role = "CONVEYOR"
        mock_context.config = {
            "wms_rcs_rack_supply_url": "http://wms-rcs/api/rack-supply",
            "smt_full_box_release_device_code": "SMT-FULL-BOX-EVENT",
        }
        mock_context.session.context_json = {
            "reel_diameter": "178.5",
            "six_in_one": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": pkg_id,
            },
            "active_bin_rack": {
                "rack_id": "RACK-FULL-001",
                "rack_code": "RACK-FULL-001",
                "cells": [
                    {
                        "rack_id": "RACK-FULL-001",
                        "rack_slot_code": "A",
                        "rack_slot_location_code": "NHW-1CLJ-0096-1A-0",
                        "bin_id": "BIN-FULL-001",
                        "bin_orientation_code": "BIN-FULL-001-A",
                        "bin_type": "6格箱",
                        "bin_cell_location": "1",
                        "status": "OCCUPIED",
                        "DateCode": "122624",
                        "LotCode": "8904936031",
                    },
                    {
                        "rack_id": "RACK-FULL-001",
                        "rack_slot_code": "B",
                        "rack_slot_location_code": "NHW-1CLJ-0096-1B-0",
                        "bin_id": "BIN-FULL-002",
                        "bin_orientation_code": "BIN-FULL-002-A",
                        "bin_type": "6格箱",
                        "bin_cell_location": "2",
                        "status": "OCCUPIED",
                        "DateCode": "122625",
                        "LotCode": "DIFFERENT",
                    },
                ],
            },
        }
        mock_context.services = WorklineRuntimeServices(bin_allocator=SmtRackBinSchedulingService())

        payload = _command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": pkg_id})
        payload["device_code"] = "PIPELINE02"
        result = await plugin.on_command_result(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "ACTIVE_RACK_SNAPSHOT_INVALID"
        assert result[0].message == "SMT 可用货架快照必须包含 A/B/C/D 4 个料箱"
        assert all(
            intent.kind != RuntimeIntentKind.COMMAND
            or intent.device_role != "OUTPUT_ARM"
            or intent.action != "PICK_AND_PUT"
            for intent in result
        )
        assert all(intent.kind != RuntimeIntentKind.EXTERNAL_REQUEST for intent in result)
        assert all(intent.kind != RuntimeIntentKind.DEVICE_EVENT for intent in result)

    @pytest.mark.asyncio
    async def test_external_rack_exchange_progress_keeps_waiting(self, plugin, mock_context):
        """WMS/RCS 换架进度回调只更新上下文，继续等待空架到位。"""
        dispatch_key = "external:smt_classifier:trace-rack-001:RACK_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "rack_supply": {
                "status": "REQUESTED",
                "dispatch_key": dispatch_key,
            }
        }

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_EXCHANGE_PROGRESS",
                    dispatch_key=dispatch_key,
                    status="IN_PROGRESS",
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT]
        assert result[0].context_patch["rack_supply"]["status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_external_rack_exchange_callback_requires_wms_rcs_envelope(self, plugin, mock_context):
        """插件直连处理 WMS/RCS 回调时也必须校验第零阶段最小包络。"""
        dispatch_key = "external:smt_classifier:trace-rack-envelope:RACK_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "rack_supply": {
                "status": "REQUESTED",
                "dispatch_key": dispatch_key,
            }
        }

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                {
                    "callback_type": "WMS_RACK_EXCHANGE_PROGRESS",
                    "dispatch_key": dispatch_key,
                    "source_system": "WMS",
                    "status": "IN_PROGRESS",
                }
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].reason_code == "PAYLOAD_INVALID"
        assert "source_event_id" in str(result[0].message)

    @pytest.mark.asyncio
    async def test_external_rack_arrived_reallocates_and_commands_output_arm(self, plugin, mock_context):
        """空架到位后基于 WMS/RCS 回传快照重新分配，并恢复出料臂搬运。"""
        pkg_id = "SVYU00125TP4LCR02_2"
        dispatch_key = "external:smt_classifier:trace-rack-002:RACK_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.workline = MagicMock(line_code="WL-SMT-001")
        active_bin_rack = {
            "rack_id": "RACK-EMPTY-001",
            "rack_code": "RACK-EMPTY-001",
            "cells": [
                {
                    "rack_id": "RACK-EMPTY-001",
                    "rack_slot_code": "C",
                    "rack_slot_location_code": "NHW-1CLJ-0097-1C-1",
                    "bin_id": "BIN-EMPTY-001",
                    "bin_orientation_code": "BIN-EMPTY-001-A",
                    "bin_type": "6格箱",
                    "bin_cell_location": "4",
                    "status": "EMPTY",
                },
                {
                    "rack_id": "RACK-EMPTY-001",
                    "rack_slot_code": "A",
                    "rack_slot_location_code": "NHW-1CLJ-0097-1A-0",
                    "bin_id": "BIN-EMPTY-A",
                    "bin_orientation_code": "BIN-EMPTY-A-A",
                    "bin_type": "6格箱",
                    "bin_cell_location": "1",
                    "status": "EMPTY",
                },
                {
                    "rack_id": "RACK-EMPTY-001",
                    "rack_slot_code": "B",
                    "rack_slot_location_code": "NHW-1CLJ-0097-1B-0",
                    "bin_id": "BIN-EMPTY-B",
                    "bin_orientation_code": "BIN-EMPTY-B-A",
                    "bin_type": "6格箱",
                    "bin_cell_location": "1",
                    "status": "EMPTY",
                },
                {
                    "rack_id": "RACK-EMPTY-001",
                    "rack_slot_code": "D",
                    "rack_slot_location_code": "NHW-1CLJ-0097-1D-1",
                    "bin_id": "BIN-EMPTY-D",
                    "bin_orientation_code": "BIN-EMPTY-D-A",
                    "bin_type": "6格箱",
                    "bin_cell_location": "1",
                    "status": "EMPTY",
                },
            ],
        }
        mock_context.session.context_json = {
            "pkg_id": pkg_id,
            "reel_diameter": "178.5",
            "six_in_one": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": pkg_id,
            },
            "rack_supply": {
                "status": "REQUESTED",
                "dispatch_key": dispatch_key,
                "pkg_id": pkg_id,
            },
        }
        mock_context.services = WorklineRuntimeServices(bin_allocator=SmtRackBinSchedulingService())
        assert "full_box_exchange" not in mock_context.session.context_json

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_ARRIVED",
                    dispatch_key=dispatch_key,
                    active_bin_rack=active_bin_rack,
                )
            ),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.RESOURCE_FACT,
            RuntimeIntentKind.RESOURCE_FACT,
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.RESOURCE_RESERVATION,
            RuntimeIntentKind.COMMAND,
        ]
        assert result[0].action == "RACK_ARRIVED"
        assert result[0].payload_json["rack_code"] == "RACK-EMPTY-001"
        assert result[0].payload_json["workline_code"] == "WL-SMT-001"
        assert result[1].action == "BIN_MOUNTED"
        assert len(result[1].payload_json["bin_mounts"]) == 4
        assert result[2].context_patch["active_bin_rack"] == active_bin_rack
        assert result[2].context_patch["rack_supply"]["status"] == "ARRIVED"
        assert "full_box_exchange" not in result[2].context_patch
        assert "full_box_exchange" not in mock_context.session.context_json
        assert result[2].context_patch["bin_location"] == {
            "rack_id": "RACK-EMPTY-001",
            "rack_slot_code": "C",
            "rack_slot_location_code": "NHW-1CLJ-0097-1C-1",
            "bin_id": "BIN-EMPTY-001",
            "bin_orientation_code": "BIN-EMPTY-001-A",
            "bin_type": "6格箱",
            "bin_cell_location": "BIN-EMPTY-001-4",
            "bin_cell_index": "4",
        }
        _assert_bin_cell_reservation(result[3], pkg_code=pkg_id, bin_code="BIN-EMPTY-001", bin_cell_index="4")
        _assert_command(result[4], action="PICK_AND_PUT", device_role="OUTPUT_ARM")
        assert result[4].payload_json["barcode"] == pkg_id
        assert result[4].payload_json["rack_slot_code"] == "C"
        assert result[4].payload_json["rack_slot_location_code"] == "NHW-1CLJ-0097-1C-1"
        assert result[4].payload_json["bin_id"] == "BIN-EMPTY-001"
        assert result[4].payload_json["bin_type"] == "6格箱"
        assert result[4].payload_json["target_loc"] == "BIN-EMPTY-001"
        assert result[4].payload_json["bin_cell_location"] == "BIN-EMPTY-001-4"
        assert result[4].payload_json["bin_cell_index"] == "4"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rack_supply_patch", "payload_patch"),
        [
            ({"target_position_code": "SINGLE_LAYER_A"}, {"position_code": "SINGLE_LAYER_B"}),
            ({"workline_code": "WL-SMT-001"}, {"workline_code": "WL-SMT-OTHER"}),
        ],
    )
    async def test_external_rack_arrived_blocks_mismatched_session_target(
        self,
        plugin,
        mock_context,
        rack_supply_patch,
        payload_patch,
    ):
        """WMS/RCS 回调显式位置必须与当前等待 session 的目标位一致。"""
        dispatch_key = "external:smt_classifier:trace-rack-target:RACK_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "pkg_id": "SVYU00125TP4LCR02_2",
            "rack_supply": {
                "status": "REQUESTED",
                "dispatch_key": dispatch_key,
                "pkg_id": "SVYU00125TP4LCR02_2",
                **rack_supply_patch,
            },
        }

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_ARRIVED",
                    dispatch_key=dispatch_key,
                    active_bin_rack={"rack_id": "RACK-EMPTY-001", "rack_code": "RACK-EMPTY-001", "cells": []},
                    **payload_patch,
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].reason_code == "RACK_SUPPLY_TARGET_MISMATCH"

    @pytest.mark.asyncio
    async def test_external_rack_arrived_blocks_partial_rack_snapshot(self, plugin, mock_context):
        """WMS/RCS 回传可用货架不足 4 个料箱时阻断，不进行料格分配。"""
        pkg_id = "SVYU00125TP4LCR02_2"
        dispatch_key = "external:smt_classifier:trace-rack-partial:RACK_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "pkg_id": pkg_id,
            "reel_diameter": "178.5",
            "six_in_one": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": pkg_id,
            },
            "rack_supply": {
                "status": "REQUESTED",
                "dispatch_key": dispatch_key,
                "pkg_id": pkg_id,
            },
        }
        mock_context.services = WorklineRuntimeServices(bin_allocator=SmtRackBinSchedulingService())

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_ARRIVED",
                    dispatch_key=dispatch_key,
                    active_bin_rack={
                        "rack_id": "RACK-PARTIAL-001",
                        "rack_code": "RACK-PARTIAL-001",
                        "cells": [
                            {
                                "rack_id": "RACK-PARTIAL-001",
                                "rack_slot_code": "A",
                                "rack_slot_location_code": "RACK-PARTIAL-001-1A-0",
                                "bin_id": "BIN-PARTIAL-001",
                                "bin_orientation_code": "BIN-PARTIAL-001-A",
                                "bin_type": "6格箱",
                                "bin_cell_location": "1",
                                "status": "EMPTY",
                            }
                        ],
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].reason_code == "ACTIVE_RACK_SNAPSHOT_INVALID"
        assert str(result[0].message) == "SMT 可用货架快照必须包含 A/B/C/D 4 个料箱"

    @pytest.mark.asyncio
    async def test_external_rack_arrived_blocks_non_empty_supply_rack(self, plugin, mock_context):
        """WMS/RCS 回传的可用货架存在非空料格时阻断，不进行料格分配。"""
        pkg_id = "SVYU00125TP4LCR02_2"
        dispatch_key = "external:smt_classifier:trace-rack-not-empty:RACK_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "pkg_id": pkg_id,
            "reel_diameter": "178.5",
            "six_in_one": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
                "PkgID": pkg_id,
            },
            "rack_supply": {
                "status": "REQUESTED",
                "dispatch_key": dispatch_key,
                "pkg_id": pkg_id,
            },
        }
        mock_context.services = WorklineRuntimeServices(bin_allocator=SmtRackBinSchedulingService())

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_ARRIVED",
                    dispatch_key=dispatch_key,
                    active_bin_rack={
                        "rack_id": "RACK-NOT-EMPTY-001",
                        "rack_code": "RACK-NOT-EMPTY-001",
                        "cells": [
                            {
                                "rack_id": "RACK-NOT-EMPTY-001",
                                "rack_slot_code": "A",
                                "rack_slot_location_code": "RACK-NOT-EMPTY-001-1A-0",
                                "bin_id": "BIN-NOT-EMPTY-A",
                                "bin_orientation_code": "BIN-NOT-EMPTY-A-A",
                                "bin_type": "6格箱",
                                "bin_cell_location": "1",
                                "status": "OCCUPIED",
                                "DateCode": "122624",
                                "LotCode": "DIFFERENT",
                            },
                            {
                                "rack_id": "RACK-NOT-EMPTY-001",
                                "rack_slot_code": "B",
                                "rack_slot_location_code": "RACK-NOT-EMPTY-001-1B-0",
                                "bin_id": "BIN-NOT-EMPTY-B",
                                "bin_orientation_code": "BIN-NOT-EMPTY-B-A",
                                "bin_type": "6格箱",
                                "bin_cell_location": "1",
                                "status": "EMPTY",
                            },
                            {
                                "rack_id": "RACK-NOT-EMPTY-001",
                                "rack_slot_code": "C",
                                "rack_slot_location_code": "RACK-NOT-EMPTY-001-1C-1",
                                "bin_id": "BIN-NOT-EMPTY-C",
                                "bin_orientation_code": "BIN-NOT-EMPTY-C-A",
                                "bin_type": "6格箱",
                                "bin_cell_location": "1",
                                "status": "EMPTY",
                            },
                            {
                                "rack_id": "RACK-NOT-EMPTY-001",
                                "rack_slot_code": "D",
                                "rack_slot_location_code": "RACK-NOT-EMPTY-001-1D-1",
                                "bin_id": "BIN-NOT-EMPTY-D",
                                "bin_orientation_code": "BIN-NOT-EMPTY-D-A",
                                "bin_type": "6格箱",
                                "bin_cell_location": "1",
                                "status": "EMPTY",
                            },
                        ],
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].reason_code == "ACTIVE_RACK_NOT_EMPTY"
        assert str(result[0].message) == "SMT 可用货架料箱必须全为空料格"

    @pytest.mark.asyncio
    async def test_external_rack_exchange_failed_blocks_material(self, plugin, mock_context):
        """WMS/RCS 换架失败回调阻断当前物料，并保留外部失败原因。"""
        dispatch_key = "external:smt_classifier:trace-rack-003:RACK_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "rack_supply": {
                "status": "REQUESTED",
                "dispatch_key": dispatch_key,
            }
        }

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_EXCHANGE_FAILED",
                    dispatch_key=dispatch_key,
                    reason_code="RCS_RACK_SUPPLY_FAILED",
                    reason_message="RCS 未能补充空架",
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "RCS_RACK_SUPPLY_FAILED"
        assert result[0].message == "RCS 未能补充空架"

    @pytest.mark.asyncio
    async def test_external_rack_arrived_duplicate_keeps_terminal_context_without_command(self, plugin, mock_context):
        """重复/迟到空架到位回调不能再次下发出料臂命令。"""
        dispatch_key = "external:smt_classifier:trace-rack-004:RACK_SUPPLY"
        mock_context.session.current_wait_type = None
        mock_context.session.context_json = {
            "rack_supply": {
                "status": "ARRIVED",
                "dispatch_key": dispatch_key,
                "pkg_id": "SVYU00125TP4LCR02_2",
            }
        }

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_ARRIVED",
                    dispatch_key=dispatch_key,
                    active_bin_rack={
                        "rack_id": "RACK-EMPTY-DUP",
                        "cells": [
                            {
                                "rack_slot_code": "A",
                                "rack_slot_location_code": "NHW-1CLJ-0098-1A-0",
                                "bin_id": "BIN-DUP-001",
                                "bin_orientation_code": "BIN-DUP-001-A",
                                "bin_type": "6格箱",
                                "bin_cell_location": "1",
                                "status": "EMPTY",
                            }
                        ],
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT]
        assert result[0].context_patch["rack_supply"]["status"] == "ARRIVED"
        assert all(
            intent.kind != RuntimeIntentKind.COMMAND
            or intent.device_role != "OUTPUT_ARM"
            or intent.action != "PICK_AND_PUT"
            for intent in result
        )

    @pytest.mark.asyncio
    async def test_external_rack_exchange_progress_late_does_not_overwrite_arrived(self, plugin, mock_context):
        """迟到进度回调不能把 ARRIVED 终态覆盖回 IN_PROGRESS。"""
        dispatch_key = "external:smt_classifier:trace-rack-005:RACK_SUPPLY"
        mock_context.session.current_wait_type = None
        mock_context.session.context_json = {
            "rack_supply": {
                "status": "ARRIVED",
                "dispatch_key": dispatch_key,
            }
        }

        result = await plugin.on_external_http(
            mock_context,
            _make_inbox(
                _wms_callback_payload(
                    callback_type="WMS_RACK_EXCHANGE_PROGRESS",
                    dispatch_key=dispatch_key,
                    status="IN_PROGRESS",
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT]
        assert result[0].context_patch["rack_supply"]["status"] == "ARRIVED"

    @pytest.mark.asyncio
    async def test_conveyor_success_blocks_material_when_scheduler_blocks_it(self, plugin, mock_context):
        """料箱调度明确阻断时，插件阻断当前物料，不再下发出料臂命令。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> SmtRackBinSchedulingDecision:
                assert barcode == "CALLBACK-PKG-004"
                return SmtRackBinSchedulingDecision(
                    kind="BLOCKED",
                    reason_code="BIN_SCHEDULING_BLOCKED",
                    message="料箱调度缺少可用目标",
                )

            def allocate(self, barcode: str) -> dict:
                raise AssertionError(f"不应在 plan_allocation 已返回阻断决策后调用 allocate: {barcode}")

        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-004"})),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "BIN_SCHEDULING_BLOCKED"
        assert result[0].message == "料箱调度缺少可用目标"

    @pytest.mark.asyncio
    async def test_conveyor_success_blocks_material_from_mapping_decision(self, plugin, mock_context):
        """mapping 形式的新阻断决策也阻断当前物料。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> dict:
                assert barcode == "CALLBACK-PKG-006"
                return {
                    "kind": "BLOCKED",
                    "reason_code": "BIN_SCHEDULING_BLOCKED",
                    "message": "mapping 调度阻断",
                }

        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-006"})),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "BIN_SCHEDULING_BLOCKED"
        assert result[0].message == "mapping 调度阻断"

    @pytest.mark.asyncio
    async def test_conveyor_success_requires_callback_pkg_id(self, plugin, mock_context):
        """流水线成功回调缺少 data.pkg_id 时返回 PAYLOAD_INVALID BLOCK。"""

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={})),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "PAYLOAD_INVALID"
        assert result[0].message == "MOVE_FORWARD 成功回调缺少 pkg_id"

    @pytest.mark.asyncio
    async def test_conveyor_failed_blocks_device_with_failure_reason(self, plugin, mock_context):
        """流水线失败返回 DEVICE BLOCK，并保留错误码。"""

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(
                _command_payload(
                    "MOVE_FORWARD",
                    "FAILED",
                    error_detail={"error_code": "CONVEYOR_ERROR", "error_message": "流水线卡住"},
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.DEVICE
        assert result[0].reason_code == "CONVEYOR_ERROR"
        assert result[0].message == "流水线卡住"

    @pytest.mark.asyncio
    async def test_conveyor_failed_requires_nested_error_detail(self, plugin, mock_context):
        """流水线失败回调不接受拍平顶层错误字段。"""
        payload = {
            "command_code": "CMD-003A",
            "command_type": "MOVE_FORWARD",
            "result": "FAILED",
            "device_code": "CONVEYOR01",
            "error_code": "CONVEYOR_ERROR",
            "error_message": "流水线卡住",
        }

        result = await plugin.on_command_result(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "PAYLOAD_INVALID"
        assert result[0].message == "MOVE_FORWARD 失败回调缺少 error_detail 字段"

    @pytest.mark.asyncio
    async def test_pick_result_rejects_legacy_command_id_and_device_id(self, plugin, mock_context):
        """命令结果不再接受 legacy command_id / device_id。"""
        mock_context.source_device_role = "INPUT_ARM"

        payload = {
            "command_id": "CMD-001",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "device_id": "ARM01",
        }

        result = await plugin.on_command_result(mock_context, _make_inbox(payload))

        assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
        assert result[0].block_scope == BlockScope.MATERIAL
        assert result[0].reason_code == "PAYLOAD_INVALID"

    @pytest.mark.asyncio
    async def test_timeout(self, plugin, mock_context):
        """插件不处理系统 timeout。"""
        assert not hasattr(plugin, "on" + "_timeout")
