"""粗分机插件扫码入口测试。"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService
from src.app.workline.models.inbox import WorklineInbox
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MEASUREMENT_REEL,
    ACTION_MOVE_TO_NG,
    EVENT_SCAN_COMPLETED,
    PHASE_MEASURING,
    PHASE_NG_MOVING,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)
from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin
from src.workline_runtime.plugin_context import PluginContext
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult
from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntentKind


def _ctx(**overrides: Any) -> PluginContext:
    values: dict[str, Any] = {
        "trace_id": "trace-rough-sorter-001",
        "config": {},
        "logger": SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
        "normalized_input": None,
    }
    values.update(overrides)
    return cast("PluginContext", SimpleNamespace(**values))


def _scan_payload(pkg_id: str = "PKG-ROUGH-001") -> dict[str, Any]:
    return {
        "device_code": "RS-SCAN-01",
        "event_type": EVENT_SCAN_COMPLETED,
        "data": {
            "HHPN": "HH-001",
            "MfrPN": "MFR-001",
            "Qty": "1500",
            "DateCode": "260528",
            "LotCode": "LOT-A",
            "PkgID": pkg_id,
        },
    }


def _inbox(payload: dict[str, Any]) -> WorklineInbox:
    return cast("WorklineInbox", SimpleNamespace(id=1, kind="DEVICE_EVENT", payload_json=payload))


@pytest.mark.asyncio
async def test_scan_ok_updates_context_and_dispatches_measurement_command() -> None:
    intents = await RoughSorterPlugin().on_device_event(_ctx(), _inbox(_scan_payload()))

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    assert intents[0].context_patch["phase"] == PHASE_MEASURING
    assert intents[0].context_patch["six_in_one"]["PkgID"] == "PKG-ROUGH-001"
    assert intents[1].action == ACTION_MEASUREMENT_REEL
    assert intents[1].device_role == ROLE_INPUT_ARM
    assert intents[1].payload_json["task_type"] == "TEST"
    assert intents[1].payload_json["params"]["action"] == ACTION_MEASUREMENT_REEL


@pytest.mark.asyncio
async def test_scan_ng_marks_material_ng_and_dispatches_move_to_ng() -> None:
    intents = await RoughSorterPlugin().on_device_event(_ctx(), _inbox(_scan_payload(pkg_id="PKG-SIZENG-001")))

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[0].context_patch["phase"] == PHASE_NG_MOVING
    assert intents[0].context_patch["ng_reason"]["reason_code"] == "SCAN_NG_BY_RULE"
    assert intents[1].reason_code == "SCAN_NG_BY_RULE"
    assert intents[2].action == ACTION_MOVE_TO_NG
    assert intents[2].device_role == ROLE_OUTPUT_ARM
    assert intents[2].payload_json["params"]["action"] == ACTION_MOVE_TO_NG
    assert intents[2].payload_json["params"]["source_location"] == "RS-SCAN-01"


@pytest.mark.asyncio
async def test_measurement_callback_routes_by_command_params_action_when_device_reports_test_task_type() -> None:
    command = SimpleNamespace(task_type="TEST", params={"action": ACTION_MEASUREMENT_REEL})
    resolved_action = CallbackOrchestrationService()._resolve_command_type(
        {"task_type": "TEST"},
        command.params,
        command,
    )
    ctx = _ctx(
        normalized_input=NormalizedCommandResult(
            command_code="CMD-MEASURE-001",
            source_result="SUCCESS",
            normalized_result="SUCCESS",
            command_type=resolved_action,
        )
    )
    inbox = cast(
        "WorklineInbox",
        SimpleNamespace(
            id=2,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-MEASURE-001",
                "task_type": "TEST",
                "result": "SUCCESS",
            },
        ),
    )

    intents = await RoughSorterPlugin().on_command_result(ctx, inbox)

    assert resolved_action == ACTION_MEASUREMENT_REEL
    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].block_scope == BlockScope.MATERIAL
    assert intents[0].reason_code == "ROUGH_SORTER_MEASUREMENT_HANDLER_NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_measurement_failed_callback_blocks_explicitly_until_task3_handler_ships() -> None:
    ctx = _ctx(
        normalized_input=NormalizedCommandResult(
            command_code="CMD-MEASURE-FAILED",
            source_result="FAILED",
            normalized_result="TERMINAL_FAILURE",
            command_type=ACTION_MEASUREMENT_REEL,
        )
    )
    inbox = cast(
        "WorklineInbox",
        SimpleNamespace(
            id=3,
            kind="COMMAND_RESULT",
            payload_json={
                "command_code": "CMD-MEASURE-FAILED",
                "task_type": "TEST",
                "result": "FAILED",
            },
        ),
    )

    intents = await RoughSorterPlugin().on_command_result(ctx, inbox)

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.BLOCK]
    assert intents[0].block_scope == BlockScope.MATERIAL
    assert intents[0].reason_code == "ROUGH_SORTER_MEASUREMENT_HANDLER_NOT_IMPLEMENTED"
