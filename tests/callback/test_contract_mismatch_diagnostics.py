"""CONTRACT_MISMATCH 当前排障合同。"""

from types import SimpleNamespace

import pytest

from src.app.callback.contracts.builder import build_diagnostic_card as build_callback_card
from src.app.callback.contracts.builder import build_diagnostic_context as build_callback_context
from src.app.callback.contracts.builder import build_diagnostic_event as build_callback_event
from src.app.callback.contracts.codes import ErrorCode as CallbackErrorCode
from src.app.callback.contracts.models import DiagnosticContext as CallbackDiagnosticContext
from src.app.callback.contracts.registry import get_diagnostic_code_definition as get_callback_definition
from src.app.runtime.orchestration.diagnostics.builder import build_diagnostic_card as build_runtime_card
from src.app.runtime.orchestration.diagnostics.builder import build_diagnostic_context as build_runtime_context
from src.app.runtime.orchestration.diagnostics.builder import build_diagnostic_event as build_runtime_event
from src.app.runtime.orchestration.diagnostics.codes import ErrorCode as RuntimeErrorCode
from src.app.runtime.orchestration.diagnostics.models import DiagnosticContext as RuntimeDiagnosticContext
from src.app.runtime.orchestration.diagnostics.registry import get_diagnostic_code_definition as get_runtime_definition
from src.app.workline.services.diagnostic_service import WorklineDiagnosticService


@pytest.mark.parametrize("builder", [build_callback_context, build_runtime_context])
def test_diagnostic_context_ignores_retired_session_and_workline_plugin_identity(builder) -> None:
    context = builder(
        session=SimpleNamespace(id=11, workline_id=7, plugin_key="poison-session"),
        workline=SimpleNamespace(id=7, line_code="LINE-07", plugin_key="poison-workline"),
    )

    assert context.plugin_key is None


def test_contract_mismatch_diagnostics_do_not_reference_retired_plugin_contracts() -> None:
    callback_event = build_callback_event(
        error_code=CallbackErrorCode.CONTRACT_MISMATCH,
        context=CallbackDiagnosticContext(),
        message="设备回调身份不匹配",
    )
    runtime_event = build_runtime_event(
        error_code=RuntimeErrorCode.CONTRACT_MISMATCH,
        context=RuntimeDiagnosticContext(),
        message="设备回调身份不匹配",
    )
    callback_definition = get_callback_definition(CallbackErrorCode.CONTRACT_MISMATCH)
    runtime_definition = get_runtime_definition(RuntimeErrorCode.CONTRACT_MISMATCH)

    assert callback_event.operator_action == callback_definition.operator_action
    assert build_callback_card(callback_event).operator_action == callback_definition.operator_action
    assert runtime_event.operator_action == runtime_definition.operator_action
    assert build_runtime_card(runtime_event).operator_action == runtime_definition.operator_action

    diagnostic_text = " ".join(
        [
            callback_event.user_message or "",
            *callback_event.next_steps,
            runtime_event.user_message or "",
            *runtime_event.next_steps,
            callback_event.operator_action or "",
            callback_definition.cause,
            callback_definition.fix,
            runtime_event.operator_action or "",
            runtime_definition.cause,
            runtime_definition.fix,
        ]
    )

    assert "归属" in diagnostic_text
    assert "不兼容" not in diagnostic_text
    assert "callback" not in diagnostic_text.lower()
    assert "plugin" not in diagnostic_text.lower()
    assert "插件" not in diagnostic_text
    assert "workline.contract_version" not in diagnostic_text


class _DiagnosticRepository:
    def __init__(self) -> None:
        self.data: dict[str, object] | None = None

    async def get_by_diagnostic_key(self, _db: object, _key: str) -> None:
        return None

    async def create_idempotent_by_diagnostic_key(self, _db: object, data: dict[str, object]) -> SimpleNamespace:
        self.data = data
        return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_contract_mismatch_persistence_keeps_operator_action_in_row_and_card() -> None:
    repository = _DiagnosticRepository()
    service = WorklineDiagnosticService(repository=repository)  # type: ignore[arg-type]
    event = build_callback_event(
        error_code=CallbackErrorCode.CONTRACT_MISMATCH,
        context=CallbackDiagnosticContext(),
        message="事件没有有效 owner",
    )

    stored = await service.record_event(object(), event=event, auto_commit=False)
    expected = get_runtime_definition(RuntimeErrorCode.CONTRACT_MISMATCH).operator_action

    assert stored.operator_action == expected
    assert stored.card_json["operator_action"] == expected
