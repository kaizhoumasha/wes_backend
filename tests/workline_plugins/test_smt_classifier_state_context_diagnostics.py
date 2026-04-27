"""SMT 插件状态机、上下文和诊断入口测试。"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier import (
    SmtClassifierContext,
    SmtClassifierPlugin,
    SmtClassifierState,
    SmtClassifierStateMachine,
    diagnose_smt_payload,
)
from src.workline_runtime.transition_validator import TransitionValidator


def _make_context(*, plugin_state: str = SmtClassifierState.IDLE) -> MagicMock:
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.id = 42
    ctx.session.context_json = {"plugin_state": plugin_state}
    ctx.trace_id = "trace-diagnostic"
    ctx.normalized_input = None
    ctx.logger = logging.getLogger("test_smt_diagnostic")
    return ctx


def test_smt_state_machine_accepts_current_plugin_triggers() -> None:
    """状态机应接受当前插件实际产出的触发器。"""

    validator = TransitionValidator()

    assert validator.validate(SmtClassifierState.IDLE, "scan_ok", SmtClassifierStateMachine) == (True, None)
    assert validator.validate(SmtClassifierState.WAITING_MEASUREMENT, "pick_ok", SmtClassifierStateMachine) == (
        True,
        None,
    )
    assert validator.validate(SmtClassifierState.WAITING_PICK_PLACE, "inspection_ng", SmtClassifierStateMachine) == (
        True,
        None,
    )
    assert validator.validate(SmtClassifierState.WAITING_OUTPUT, "manual_hold", SmtClassifierStateMachine) == (
        True,
        None,
    )


def test_smt_state_machine_rejects_invalid_transition() -> None:
    """非法迁移应由 runtime TransitionValidator 拦截。"""

    is_valid, error = TransitionValidator().validate(
        SmtClassifierState.WAITING_OUTPUT,
        "scan_ok",
        SmtClassifierStateMachine,
    )

    assert is_valid is False
    assert error is not None
    assert "Invalid transition" in error


def test_smt_context_projects_typed_patch() -> None:
    """SMT 上下文应能从 session.context_json 解析并投影为 patch。"""

    context = SmtClassifierContext.from_mapping(
        {
            "plugin_state": SmtClassifierState.WAITING_CONVEYOR,
            "barcode": "PKG-001",
            "barcodes": ["PKG-001"],
            "reel_diameter": 178.5,
        }
    )

    patch = context.to_patch(plugin_state=SmtClassifierState.WAITING_OUTPUT)

    assert context.plugin_state == SmtClassifierState.WAITING_CONVEYOR
    assert context.barcode == "PKG-001"
    assert patch["plugin_state"] == SmtClassifierState.WAITING_OUTPUT
    assert patch["reel_diameter"] == 178.5


@pytest.mark.asyncio
async def test_smt_payload_diagnostic_explains_handler_context_and_result() -> None:
    """插件级诊断应解释输入、上下文、命中的 handler 和 PluginResult。"""

    plugin = SmtClassifierPlugin()
    ctx = _make_context()
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

    diagnostic = await diagnose_smt_payload(plugin, ctx, payload, kind="DEVICE_EVENT")

    assert diagnostic.normalized_input["canonical_event_type"] == "SCAN_COMPLETED"
    assert diagnostic.parsed_context.plugin_state == SmtClassifierState.IDLE
    assert diagnostic.selected_handler == "handle_scan_completed"
    assert diagnostic.plugin_result.transition == "scan_ok"
    assert diagnostic.plugin_result.context_patch["plugin_state"] == SmtClassifierState.WAITING_MEASUREMENT
