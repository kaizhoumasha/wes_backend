"""入库料箱称重复核插件 spike 测试。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.workline_plugins.inbound_tote_qc import (
    InboundToteQcContext,
    InboundToteQcPlugin,
)
from src.workline_plugins.inbound_tote_qc.contract import (
    build_divert_tote_params,
    build_weigh_tote_params,
    resolve_tote_business_key,
)
from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.runtime_intent import BlockScope, DestinationKind, RuntimeIntentKind


def _make_context(**context: object) -> MagicMock:
    ctx = MagicMock()
    ctx.session = MagicMock(id=42)
    ctx.session.context_json = context
    ctx.trace_id = "trace-inbound-tote"
    ctx.normalized_input = None
    ctx.next = PluginNext()
    ctx.logger = logging.getLogger("test_inbound_tote_qc")
    return ctx


def _make_inbox(payload_json: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(id=1, payload_json=payload_json, kind=None, trace_id="trace-inbound-tote")


def test_inbound_tote_manifest_declares_independent_platform_contract() -> None:
    """第二插件 manifest 应独立声明业务键、设备角色和 context。"""

    manifest = InboundToteQcPlugin.manifest

    assert manifest.plugin_key == "inbound_tote_qc"
    assert manifest.contract_version == "spike-2026.04"
    assert not hasattr(manifest, "state" + "_machine_class")
    assert manifest.context_model is InboundToteQcContext
    assert (
        manifest.resolve_business_key(
            {
                "event_type": "TOTE_ARRIVED",
                "data": {"tote_id": "TOTE-001"},
            }
        )
        == "TOTE-001"
    )
    assert manifest.event_source_roles == {"TOTE_ARRIVED": ("ENTRY_SCANNER",)}
    assert manifest.command_target_roles == {
        "WEIGH_TOTE": ("WEIGH_SCALE",),
        "DIVERT_TOTE": ("DIVERT_CONVEYOR",),
    }


def test_inbound_tote_contract_helpers_keep_business_data_in_params() -> None:
    """第二插件命令 helper 只返回业务 params。"""

    assert resolve_tote_business_key({"data": {"tote_id": "TOTE-001"}}) == "TOTE-001"
    assert build_weigh_tote_params(tote_id="TOTE-001", station_code="INBOUND_QC_01") == {
        "tote_id": "TOTE-001",
        "station_code": "INBOUND_QC_01",
    }
    assert build_divert_tote_params(
        tote_id="TOTE-001",
        destination_lane="HOLD_LANE",
        reason_code="WEIGHT_OUT_OF_TOLERANCE",
    ) == {
        "tote_id": "TOTE-001",
        "destination_lane": "HOLD_LANE",
        "reason_code": "WEIGHT_OUT_OF_TOLERANCE",
    }


@pytest.mark.asyncio
async def test_tote_arrived_creates_weigh_command() -> None:
    """TOTE_ARRIVED 应更新上下文并下发称重 RuntimeIntent。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context()
    inbox = _make_inbox(
        {
            "device_code": "SCAN01",
            "event_type": "TOTE_ARRIVED",
            "data": {
                "tote_id": "TOTE-20260425-001",
                "station_code": "INBOUND_QC_01",
                "expected_weight_kg": 12.5,
                "tolerance_kg": 0.2,
            },
        }
    )

    result = await plugin.on_device_event(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert result[0].context_patch == {
        "tote_id": "TOTE-20260425-001",
        "station_code": "INBOUND_QC_01",
        "expected_weight_kg": 12.5,
        "tolerance_kg": 0.2,
    }
    assert result[1].action == "WEIGH_TOTE"
    assert result[1].device_role == "WEIGH_SCALE"
    assert result[1].destination.kind == DestinationKind.ROLE
    assert result[1].destination.value == "WEIGH_SCALE"
    assert result[1].timeout_seconds == 120
    assert result[1].payload_json == {
        "tote_id": "TOTE-20260425-001",
        "station_code": "INBOUND_QC_01",
    }


@pytest.mark.asyncio
async def test_tote_arrived_without_data_blocks_material() -> None:
    """TOTE_ARRIVED 缺少 data 时返回 MATERIAL BLOCK。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context()
    inbox = _make_inbox({"device_code": "SCAN01", "event_type": "TOTE_ARRIVED"})

    result = await plugin.on_device_event(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].block_scope == BlockScope.MATERIAL
    assert result[0].reason_code == "PAYLOAD_INVALID"
    assert result[0].message == "TOTE_ARRIVED 缺少 data 字段"


@pytest.mark.asyncio
async def test_tote_arrived_with_invalid_data_blocks_material() -> None:
    """TOTE_ARRIVED data 结构非法时也返回 MATERIAL BLOCK。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context()
    inbox = _make_inbox(
        {
            "device_code": "SCAN01",
            "event_type": "TOTE_ARRIVED",
            "data": {
                "tote_id": "TOTE-20260425-001",
                "station_code": "INBOUND_QC_01",
            },
        }
    )

    result = await plugin.on_device_event(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].block_scope == BlockScope.MATERIAL
    assert result[0].reason_code == "PAYLOAD_INVALID"
    assert result[0].message == "TOTE_ARRIVED data 非法"


@pytest.mark.asyncio
async def test_weigh_success_in_tolerance_diverts_to_pass_lane() -> None:
    """重量在允差内时放行到 PASS_LANE。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context(
        tote_id="TOTE-20260425-001",
        expected_weight_kg=12.5,
        tolerance_kg=0.2,
    )
    inbox = _make_inbox(
        {
            "command_code": "CMD-WEIGH-001",
            "command_type": "WEIGH_TOTE",
            "device_code": "SCALE01",
            "result": "SUCCESS",
            "data": {"tote_id": "TOTE-20260425-001", "actual_weight_kg": 12.58},
        }
    )

    result = await plugin.on_command_result(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert result[0].context_patch == {
        "tote_id": "TOTE-20260425-001",
        "actual_weight_kg": 12.58,
        "destination_lane": "PASS_LANE",
        "reason_code": "WEIGHT_OK",
    }
    assert result[1].action == "DIVERT_TOTE"
    assert result[1].device_role == "DIVERT_CONVEYOR"
    assert result[1].destination.kind == DestinationKind.ROLE
    assert result[1].destination.value == "DIVERT_CONVEYOR"
    assert result[1].timeout_seconds == 120
    assert result[1].payload_json == {
        "tote_id": "TOTE-20260425-001",
        "destination_lane": "PASS_LANE",
        "reason_code": "WEIGHT_OK",
    }


@pytest.mark.asyncio
async def test_weigh_success_out_of_tolerance_marks_ng_and_diverts_to_hold_lane() -> None:
    """重量超差返回 MARK_NG 并继续分流到 HOLD_LANE。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context(
        tote_id="TOTE-20260425-001",
        expected_weight_kg=12.5,
        tolerance_kg=0.2,
    )
    inbox = _make_inbox(
        {
            "command_code": "CMD-WEIGH-002",
            "command_type": "WEIGH_TOTE",
            "device_code": "SCALE01",
            "result": "SUCCESS",
            "data": {"tote_id": "TOTE-20260425-001", "actual_weight_kg": 13.1},
        }
    )

    result = await plugin.on_command_result(ctx, inbox)

    assert [intent.kind for intent in result] == [
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
    assert result[0].reason_code == "WEIGHT_OUT_OF_TOLERANCE"
    assert result[0].message == "料箱重量超出允差，分流到异常线"
    assert result[0].payload_json == {
        "expected_weight_kg": 12.5,
        "actual_weight_kg": 13.1,
        "tolerance_kg": 0.2,
        "tote_id": "TOTE-20260425-001",
    }
    assert result[1].context_patch == {
        "tote_id": "TOTE-20260425-001",
        "actual_weight_kg": 13.1,
        "destination_lane": "HOLD_LANE",
        "reason_code": "WEIGHT_OUT_OF_TOLERANCE",
    }
    assert result[2].action == "DIVERT_TOTE"
    assert result[2].payload_json == {
        "tote_id": "TOTE-20260425-001",
        "destination_lane": "HOLD_LANE",
        "reason_code": "WEIGHT_OUT_OF_TOLERANCE",
    }


@pytest.mark.asyncio
async def test_weigh_success_without_context_blocks_material() -> None:
    """称重成功但缺少上下文时返回 MATERIAL BLOCK。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context(tote_id="TOTE-20260425-001")
    inbox = _make_inbox(
        {
            "command_code": "CMD-WEIGH-002",
            "command_type": "WEIGH_TOTE",
            "device_code": "SCALE01",
            "result": "SUCCESS",
            "data": {"tote_id": "TOTE-20260425-001", "actual_weight_kg": 13.1},
        }
    )

    result = await plugin.on_command_result(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].block_scope == BlockScope.MATERIAL
    assert result[0].reason_code == "PAYLOAD_INVALID"
    assert result[0].message == "缺少料箱称重上下文"


@pytest.mark.asyncio
async def test_weigh_failed_is_hardware_failure() -> None:
    """称重设备 FAILED 应进入系统/硬件异常路径。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context()
    inbox = _make_inbox(
        {
            "command_code": "CMD-WEIGH-003",
            "command_type": "WEIGH_TOTE",
            "device_code": "SCALE01",
            "result": "FAILED",
            "error_detail": {"error_code": "SCALE_TIMEOUT", "error_message": "Scale did not complete within timeout"},
        }
    )

    result = await plugin.on_command_result(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].block_scope == BlockScope.COMMAND
    assert result[0].reason_code == "SCALE_TIMEOUT"
    assert result[0].message == "Scale did not complete within timeout"


@pytest.mark.asyncio
async def test_divert_success_completes_session() -> None:
    """分流成功后完成业务链路。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context()
    inbox = _make_inbox(
        {
            "command_code": "CMD-DIVERT-001",
            "command_type": "DIVERT_TOTE",
            "device_code": "DIVERT01",
            "result": "SUCCESS",
            "data": {"tote_id": "TOTE-20260425-001"},
        }
    )

    result = await plugin.on_command_result(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.COMPLETE]


@pytest.mark.asyncio
async def test_divert_failed_blocks_material_with_failure_reason() -> None:
    """分流失败后阻塞当前料箱并保留设备失败码。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context()
    inbox = _make_inbox(
        {
            "command_code": "CMD-DIVERT-002",
            "command_type": "DIVERT_TOTE",
            "device_code": "DIVERT01",
            "result": "FAILED",
            "error_detail": {"error_code": "LANE_JAMMED", "error_message": "料箱分流失败"},
        }
    )

    result = await plugin.on_command_result(ctx, inbox)

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].block_scope == BlockScope.MATERIAL
    assert result[0].reason_code == "LANE_JAMMED"
    assert result[0].message == "料箱分流失败"
