"""入库料箱称重复核插件 spike 测试。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.workline_plugins.inbound_tote_qc import (
    InboundToteQcContext,
    InboundToteQcPlugin,
    InboundToteQcState,
    InboundToteQcStateMachine,
)
from src.workline_plugins.inbound_tote_qc.contract import (
    build_divert_tote_params,
    build_weigh_tote_params,
    resolve_tote_business_key,
)
from src.workline_runtime.transition_validator import TransitionValidator
from src.workline_runtime.types import CommandTargetScope


def _make_context(plugin_state: str = InboundToteQcState.IDLE, **context: object) -> MagicMock:
    ctx = MagicMock()
    ctx.session = MagicMock(id=42)
    ctx.session.context_json = {"plugin_state": plugin_state, **context}
    ctx.correlation_id = "corr-inbound-tote"
    ctx.normalized_input = None
    ctx.logger = logging.getLogger("test_inbound_tote_qc")
    return ctx


def _make_inbox(payload_json: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(id=1, payload_json=payload_json, kind=None, correlation_id="corr-inbound-tote")


def test_inbound_tote_manifest_declares_independent_platform_contract() -> None:
    """第二插件 manifest 应独立声明业务键、设备角色、状态机和 context。"""

    manifest = InboundToteQcPlugin.manifest

    assert manifest.plugin_key == "inbound_tote_qc"
    assert manifest.contract_version == "spike-2026.04"
    assert manifest.state_machine_class is InboundToteQcStateMachine
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


def test_inbound_tote_state_machine_rejects_invalid_transition() -> None:
    """第二插件状态机应能被 runtime TransitionValidator 消费。"""

    validator = TransitionValidator()

    assert validator.validate(InboundToteQcState.IDLE, "tote_arrived", InboundToteQcStateMachine) == (True, None)
    is_valid, error = validator.validate(
        InboundToteQcState.WAITING_DIVERT,
        "tote_arrived",
        InboundToteQcStateMachine,
    )

    assert is_valid is False
    assert error is not None


@pytest.mark.asyncio
async def test_tote_arrived_creates_weigh_command() -> None:
    """TOTE_ARRIVED 应进入称重等待态并只输出 params。"""

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

    assert result.transition == "tote_arrived"
    assert result.failure is None
    assert result.commands[0].action == "WEIGH_TOTE"
    assert result.commands[0].target_scope == CommandTargetScope.DOWNSTREAM
    assert result.commands[0].device_role == "WEIGH_SCALE"
    assert result.commands[0].parameters == {
        "tote_id": "TOTE-20260425-001",
        "station_code": "INBOUND_QC_01",
    }
    assert result.context_patch["plugin_state"] == InboundToteQcState.WAITING_WEIGH
    assert result.wait is not None


@pytest.mark.asyncio
async def test_weigh_success_in_tolerance_diverts_to_pass_lane() -> None:
    """重量在允差内时放行到 PASS_LANE。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context(
        InboundToteQcState.WAITING_WEIGH,
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

    assert result.transition == "weight_ok"
    assert result.business_decisions == []
    assert result.commands[0].action == "DIVERT_TOTE"
    assert result.commands[0].parameters["destination_lane"] == "PASS_LANE"
    assert result.context_patch["plugin_state"] == InboundToteQcState.WAITING_DIVERT


@pytest.mark.asyncio
async def test_weigh_success_out_of_tolerance_records_business_decision() -> None:
    """重量超差是业务 NG，不应返回 failure。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context(
        InboundToteQcState.WAITING_WEIGH,
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

    assert result.transition == "weight_ng"
    assert result.failure is None
    assert result.business_decisions[0].reason_code == "WEIGHT_OUT_OF_TOLERANCE"
    assert result.business_decisions[0].business_key == "TOTE-20260425-001"
    assert result.commands[0].parameters["destination_lane"] == "HOLD_LANE"
    assert result.context_patch["plugin_state"] == InboundToteQcState.WAITING_DIVERT


@pytest.mark.asyncio
async def test_weigh_failed_is_hardware_failure() -> None:
    """称重设备 FAILED 应进入系统/硬件异常路径。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context(InboundToteQcState.WAITING_WEIGH)
    inbox = _make_inbox(
        {
            "command_code": "CMD-WEIGH-003",
            "command_type": "WEIGH_TOTE",
            "device_code": "SCALE01",
            "result": "FAILED",
            "error_detail": {"code": "SCALE_TIMEOUT", "msg": "Scale did not complete within timeout"},
        }
    )

    result = await plugin.on_command_result(ctx, inbox)

    assert result.transition == "fail"
    assert result.failure is not None
    assert result.failure.domain == "HARDWARE"
    assert result.failure.code == "SCALE_TIMEOUT"


@pytest.mark.asyncio
async def test_divert_success_completes_session() -> None:
    """分流成功后完成业务链路。"""

    plugin = InboundToteQcPlugin()
    ctx = _make_context(InboundToteQcState.WAITING_DIVERT)
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

    assert result.transition == "divert_ok"
    assert result.complete is True
    assert result.context_patch["plugin_state"] == InboundToteQcState.COMPLETED
