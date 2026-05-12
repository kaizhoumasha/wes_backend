"""SMT 插件 RuntimeIntent、上下文和诊断入口测试。"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.workline_plugins.smt_classifier import SmtClassifierContext, SmtClassifierPlugin, diagnose_smt_payload
from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.runtime_intent import RuntimeIntentKind


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.id = 42
    ctx.session.context_json = {}
    ctx.trace_id = "trace-diagnostic"
    ctx.normalized_input = None
    ctx.source_device_role = "INPUT_ARM"
    ctx.next = PluginNext()
    ctx.logger = logging.getLogger("test_smt_diagnostic")
    return ctx


def test_smt_module_no_longer_exports_state_machine() -> None:
    """SMT 插件模块不再导出 legacy state machine。"""

    smt_module = importlib.import_module("src.workline_plugins.smt_classifier")

    assert not hasattr(smt_module, "SmtClassifierState")
    assert not hasattr(smt_module, "SmtClassifierStateMachine")
    assert not hasattr(SmtClassifierPlugin.manifest, "state" + "_machine_class")
    assert not Path("src/workline_plugins/smt_classifier/state_machine.py").exists()


def test_smt_context_projects_typed_patch() -> None:
    """SMT 上下文 patch 保留插件业务字段。"""

    context = SmtClassifierContext.from_mapping(
        {
            "barcode": "PKG-001",
            "barcodes": ["PKG-001"],
            "reel_diameter": 178.5,
        }
    )

    patch = context.to_patch()

    assert context.barcode == "PKG-001"
    assert patch["reel_diameter"] == 178.5


@pytest.mark.asyncio
async def test_smt_payload_diagnostic_explains_handler_context_and_runtime_intents() -> None:
    """插件级诊断应解释输入、上下文、命中的 handler 和 RuntimeIntent 输出。"""

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
    assert diagnostic.selected_handler == "handle_scan_completed"
    assert [intent.kind for intent in diagnostic.plugin_result] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
    assert diagnostic.plugin_result[0].context_patch["barcode"] == "SVYU00125TP4LCR02_2"
