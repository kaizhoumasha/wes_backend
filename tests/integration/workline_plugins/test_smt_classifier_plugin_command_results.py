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
        "dispatch_key": "external:smt_classifier:trace-smt-command:RACK_EXCHANGE_AND_SUPPLY",
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


class TestSmtClassifierPluginCommandResults:
    """SMT 分类插件命令结果 RuntimeIntent 测试。"""

    def test_build_output_to_bin_params_includes_bin_cell_location(self):
        """出料命令参数必须携带具体料箱格。"""

        params = build_output_to_bin_params(
            pkg_id="CALLBACK-PKG-001",
            reel_diameter="178.5",
            bin_location={
                "bin_id": "BIN-001",
                "bin_type": "九格箱",
                "bin_cell_location": "6",
            },
        )

        assert params == {
            "barcode": "CALLBACK-PKG-001",
            "reel_diameter": "178.5",
            "target_type": "BIN",
            "target_loc": "BIN-001",
            "bin_id": "BIN-001",
            "bin_type": "九格箱",
            "bin_cell_location": "6",
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
        """出料臂抓取成功后完成。"""
        mock_context.source_device_role = "OUTPUT_ARM"

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("PICK_AND_PUT", "SUCCESS")),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.COMPLETE]

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

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
        assert result[0].context_patch["pkg_id"] == "CALLBACK-PKG-001"
        assert "bin_location" in result[0].context_patch
        _assert_command(result[1], action="PICK_AND_PUT", device_role="OUTPUT_ARM")
        assert result[1].payload_json["barcode"] == "CALLBACK-PKG-001"
        assert result[1].payload_json["reel_diameter"] == "178.5"
        assert result[1].payload_json["bin_id"] == result[0].context_patch["bin_location"]["bin_id"]
        assert (
            result[1].payload_json["bin_cell_location"] == result[0].context_patch["bin_location"]["bin_cell_location"]
        )
        assert result[0].context_patch["bin_location"] == SmtRackBinSchedulingService().allocate("CALLBACK-PKG-001")

    @pytest.mark.asyncio
    async def test_conveyor_success_uses_bin_allocator_service(self, plugin, mock_context):
        """料箱分配优先走 ctx.services 内部领域服务。"""

        class BinAllocator:
            def allocate(self, barcode: str) -> dict:
                assert barcode == "CALLBACK-PKG-002"
                return {
                    "bin_id": "BIN-SVC-001",
                    "bin_type": "九格箱",
                    "bin_cell_location": "6",
                }

        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())
        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-002"})),
        )

        assert result[0].context_patch["bin_location"]["bin_id"] == "BIN-SVC-001"
        assert result[1].payload_json["target_loc"] == "BIN-SVC-001"
        assert result[1].payload_json["bin_id"] == "BIN-SVC-001"
        assert result[1].payload_json["bin_type"] == "九格箱"
        assert result[1].payload_json["bin_cell_location"] == "6"

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
                    "bin_id": "BIN-SVC-003",
                    "bin_type": "九格箱",
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
                        "bin_id": "BIN-SVC-002",
                        "bin_type": "九格箱",
                        "bin_cell_location": "2",
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
                        "bin_id": "BIN-SVC-TRACE",
                        "bin_type": "九格箱",
                        "bin_cell_location": "8",
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
    async def test_conveyor_success_requests_full_box_exchange_when_scheduler_requires_it(self, plugin, mock_context):
        """料箱调度需要满箱交换时，插件只发外部请求，不直接下发出料臂命令。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> SmtRackBinSchedulingDecision:
                assert barcode == "CALLBACK-PKG-003"
                assert context["reel_diameter"] == "178.5"
                return SmtRackBinSchedulingDecision(
                    full_box_exchange_request=SmtFullBoxExchangeRequest(
                        dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
                        target_code="http://wms-rcs/api/full-box-exchange",
                        payload={
                            "exchange_request_code": "external:smt:release-001:FULL_BIN_EXCHANGE",
                            "rack_release_id": "release-001",
                            "pkg_id": barcode,
                        },
                        timeout_seconds=1800,
                        source_system="WMS_RCS",
                    )
                )

            def allocate(self, barcode: str) -> dict:
                raise AssertionError(f"不应在 plan_allocation 已返回交换决策后调用 allocate: {barcode}")

        mock_context.session.context_json = {"reel_diameter": "178.5"}
        mock_context.services = WorklineRuntimeServices(bin_allocator=BinAllocator())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": "CALLBACK-PKG-003"})),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.EXTERNAL_REQUEST,
        ]
        assert result[0].context_patch["pkg_id"] == "CALLBACK-PKG-003"
        assert result[0].context_patch["full_box_exchange"] == {
            "status": "REQUESTED",
            "dispatch_key": "external:smt:release-001:FULL_BIN_EXCHANGE",
            "target_code": "http://wms-rcs/api/full-box-exchange",
            "source_system": "WMS_RCS",
        }
        assert "rack_exchange" not in result[0].context_patch
        assert result[1].dispatch_key == "external:smt:release-001:FULL_BIN_EXCHANGE"
        assert result[1].target_code == "http://wms-rcs/api/full-box-exchange"
        assert result[1].payload_json["rack_release_id"] == "release-001"
        assert result[1].timeout_seconds == 1800

    @pytest.mark.asyncio
    async def test_conveyor_success_requests_exchange_from_mapping_decision(self, plugin, mock_context):
        """mapping 形式的新换架决策也走现有外部请求兼容分支。"""

        class BinAllocator:
            def plan_allocation(self, barcode: str, *, context: dict) -> dict:
                assert barcode == "CALLBACK-PKG-005"
                return {
                    "kind": "RACK_EXCHANGE_REQUIRED",
                    "external_request": {
                        "dispatch_key": "external:smt_classifier:trace-005:RACK_EXCHANGE_AND_SUPPLY",
                        "target_code": "http://wms-rcs/api/rack-exchange",
                        "payload": {
                            "request_type": "SMT_RACK_EXCHANGE_AND_SUPPLY",
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
        assert result[0].context_patch["full_box_exchange"]["dispatch_key"] == (
            "external:smt_classifier:trace-005:RACK_EXCHANGE_AND_SUPPLY"
        )
        assert result[1].target_code == "http://wms-rcs/api/rack-exchange"

    @pytest.mark.asyncio
    async def test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store(
        self, plugin, mock_context
    ):
        """当前货架无兼容格且无空格时，请求换出当前架并补充空架。"""

        pkg_id = "SVYU00125TP4LCR02_2"
        mock_context.trace_id = "trace-rack-full-001"
        mock_context.config = {"wms_rcs_rack_exchange_url": "http://wms-rcs/api/rack-exchange"}
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
                        "bin_id": "BIN-FULL-001",
                        "bin_type": "九格箱",
                        "bin_cell_location": "1",
                        "status": "OCCUPIED",
                        "DateCode": "122624",
                        "LotCode": "8904936031",
                    },
                    {
                        "rack_id": "RACK-FULL-001",
                        "bin_id": "BIN-FULL-002",
                        "bin_type": "九格箱",
                        "bin_cell_location": "2",
                        "status": "OCCUPIED",
                        "DateCode": "122625",
                        "LotCode": "DIFFERENT",
                    },
                ],
            },
        }
        mock_context.services = WorklineRuntimeServices(bin_allocator=SmtRackBinSchedulingService())

        result = await plugin.on_command_result(
            mock_context,
            _make_inbox(_command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": pkg_id})),
        )

        assert [intent.kind for intent in result] == [
            RuntimeIntentKind.UPDATE_CONTEXT,
            RuntimeIntentKind.EXTERNAL_REQUEST,
        ]
        assert result[0].context_patch["rack_exchange"] == {
            "status": "REQUESTED",
            "dispatch_key": result[1].dispatch_key,
            "target_code": result[1].target_code,
            "source_system": result[1].source_system,
            "reason_code": "NO_COMPATIBLE_OR_EMPTY_CELL",
            "requested_actions": ["MOVE_OUT_CURRENT_RACK", "SUPPLY_EMPTY_RACK"],
            "pkg_id": pkg_id,
        }
        assert result[1].payload_json["actions"] == ["MOVE_OUT_CURRENT_RACK", "SUPPLY_EMPTY_RACK"]
        assert result[1].payload_json["dispatch_key"] == result[1].dispatch_key
        assert result[1].payload_json["trace_id"] == "trace-rack-full-001"
        assert all(
            intent.kind != RuntimeIntentKind.COMMAND
            or intent.device_role != "OUTPUT_ARM"
            or intent.action != "PICK_AND_PUT"
            for intent in result
        )

    @pytest.mark.asyncio
    async def test_external_rack_exchange_progress_keeps_waiting(self, plugin, mock_context):
        """WMS/RCS 换架进度回调只更新上下文，继续等待空架到位。"""
        dispatch_key = "external:smt_classifier:trace-rack-001:RACK_EXCHANGE_AND_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "rack_exchange": {
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
        assert result[0].context_patch["rack_exchange"]["status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_external_rack_exchange_callback_requires_wms_rcs_envelope(self, plugin, mock_context):
        """插件直连处理 WMS/RCS 回调时也必须校验第零阶段最小包络。"""
        dispatch_key = "external:smt_classifier:trace-rack-envelope:RACK_EXCHANGE_AND_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "rack_exchange": {
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
        """空架到位后基于新 active_bin_rack 重新分配，并恢复出料臂搬运。"""
        pkg_id = "SVYU00125TP4LCR02_2"
        dispatch_key = "external:smt_classifier:trace-rack-002:RACK_EXCHANGE_AND_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        active_bin_rack = {
            "rack_id": "RACK-EMPTY-001",
            "rack_code": "RACK-EMPTY-001",
            "cells": [
                {
                    "rack_id": "RACK-EMPTY-001",
                    "bin_id": "BIN-EMPTY-001",
                    "bin_type": "九格箱",
                    "bin_cell_location": "4",
                    "status": "EMPTY",
                }
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
            "rack_exchange": {
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
                    active_bin_rack=active_bin_rack,
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
        assert result[0].context_patch["active_bin_rack"] == active_bin_rack
        assert result[0].context_patch["rack_exchange"]["status"] == "ARRIVED"
        assert result[0].context_patch["bin_location"] == {
            "rack_id": "RACK-EMPTY-001",
            "bin_id": "BIN-EMPTY-001",
            "bin_type": "九格箱",
            "bin_cell_location": "4",
        }
        _assert_command(result[1], action="PICK_AND_PUT", device_role="OUTPUT_ARM")
        assert result[1].payload_json["barcode"] == pkg_id
        assert result[1].payload_json["bin_id"] == "BIN-EMPTY-001"
        assert result[1].payload_json["bin_type"] == "九格箱"
        assert result[1].payload_json["target_loc"] == "BIN-EMPTY-001"
        assert result[1].payload_json["bin_cell_location"] == "4"

    @pytest.mark.asyncio
    async def test_external_rack_exchange_failed_blocks_material(self, plugin, mock_context):
        """WMS/RCS 换架失败回调阻断当前物料，并保留外部失败原因。"""
        dispatch_key = "external:smt_classifier:trace-rack-003:RACK_EXCHANGE_AND_SUPPLY"
        mock_context.session.current_wait_type = "EXTERNAL_HTTP"
        mock_context.session.context_json = {
            "rack_exchange": {
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
        dispatch_key = "external:smt_classifier:trace-rack-004:RACK_EXCHANGE_AND_SUPPLY"
        mock_context.session.current_wait_type = None
        mock_context.session.context_json = {
            "rack_exchange": {
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
                                "bin_id": "BIN-DUP-001",
                                "bin_type": "九格箱",
                                "bin_cell_location": "1",
                                "status": "EMPTY",
                            }
                        ],
                    },
                )
            ),
        )

        assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT]
        assert result[0].context_patch["rack_exchange"]["status"] == "ARRIVED"
        assert all(
            intent.kind != RuntimeIntentKind.COMMAND
            or intent.device_role != "OUTPUT_ARM"
            or intent.action != "PICK_AND_PUT"
            for intent in result
        )

    @pytest.mark.asyncio
    async def test_external_rack_exchange_progress_late_does_not_overwrite_arrived(self, plugin, mock_context):
        """迟到进度回调不能把 ARRIVED 终态覆盖回 IN_PROGRESS。"""
        dispatch_key = "external:smt_classifier:trace-rack-005:RACK_EXCHANGE_AND_SUPPLY"
        mock_context.session.current_wait_type = None
        mock_context.session.context_json = {
            "rack_exchange": {
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
        assert result[0].context_patch["rack_exchange"]["status"] == "ARRIVED"

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
