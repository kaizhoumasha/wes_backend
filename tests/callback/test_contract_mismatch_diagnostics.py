"""CONTRACT_MISMATCH 当前排障合同。"""

from types import SimpleNamespace

import pytest

from src.app.callback.contracts.builder import build_diagnostic_card as build_callback_card
from src.app.callback.contracts.builder import build_diagnostic_context as build_callback_context
from src.app.callback.contracts.builder import build_diagnostic_event as build_callback_event
from src.app.callback.contracts.codes import ErrorCode as CallbackErrorCode
from src.app.callback.contracts.codes import ErrorDomain as CallbackErrorDomain
from src.app.callback.contracts.failure_mapper import map_failure_to_diagnostic as map_callback_failure
from src.app.callback.contracts.models import DiagnosticContext as CallbackDiagnosticContext
from src.app.callback.contracts.registry import get_diagnostic_code_definition as get_callback_definition
from src.app.runtime.orchestration.diagnostics.builder import build_diagnostic_card as build_runtime_card
from src.app.runtime.orchestration.diagnostics.builder import build_diagnostic_context as build_runtime_context
from src.app.runtime.orchestration.diagnostics.builder import build_diagnostic_event as build_runtime_event
from src.app.runtime.orchestration.diagnostics.codes import ErrorCode as RuntimeErrorCode
from src.app.runtime.orchestration.diagnostics.codes import ErrorDomain as RuntimeErrorDomain
from src.app.runtime.orchestration.diagnostics.failure_mapper import map_failure_to_diagnostic as map_runtime_failure
from src.app.runtime.orchestration.diagnostics.models import DiagnosticContext as RuntimeDiagnosticContext
from src.app.runtime.orchestration.diagnostics.registry import get_diagnostic_code_definition as get_runtime_definition
from src.app.workline.services.diagnostic_service import WorklineDiagnosticService


@pytest.mark.parametrize("builder", [build_callback_context, build_runtime_context])
def test_diagnostic_context_excludes_retired_session_and_workline_plugin_identity(builder) -> None:
    context = builder(
        session=SimpleNamespace(id=11, workline_id=7, plugin_key="poison-session"),
        workline=SimpleNamespace(id=7, line_code="LINE-07", plugin_key="poison-workline"),
    )

    assert "plugin_key" not in type(context).model_fields
    assert "plugin_key" not in context.model_dump()


def test_callback_and_runtime_diagnostic_mirrors_exclude_retired_plugin_codes_and_domain() -> None:
    callback_codes = set(CallbackErrorCode.__members__)
    runtime_codes = set(RuntimeErrorCode.__members__)
    callback_domains = set(CallbackErrorDomain.__members__)
    runtime_domains = set(RuntimeErrorDomain.__members__)

    assert callback_codes == runtime_codes
    assert runtime_codes.isdisjoint({"PLUGIN_BINDING_REQUIRED", "PLUGIN_EXECUTION_FAILED", "PLUGIN_TRANSITION_INVALID"})
    assert {"WORKFLOW_EXECUTION_FAILED", "WORKFLOW_TRANSITION_INVALID"}.issubset(runtime_codes)
    assert callback_domains == runtime_domains
    assert "PLUGIN" not in runtime_domains


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (SimpleNamespace(code="STATE_MISMATCH", domain="ORCHESTRATION"), "WORKFLOW_TRANSITION_INVALID"),
        (SimpleNamespace(code="SOFTWARE_FAILURE", domain="SOFTWARE"), "WORKFLOW_EXECUTION_FAILED"),
        (SimpleNamespace(code="ORCHESTRATION_FAILURE", domain="ORCHESTRATION"), "WORKFLOW_EXECUTION_FAILED"),
    ],
)
def test_failure_mappers_use_workflow_diagnostics(failure: SimpleNamespace, expected_code: str) -> None:
    callback_result = map_callback_failure(failure=failure)
    runtime_result = map_runtime_failure(failure=failure)

    assert (callback_result[0].value, callback_result[1].value) == (expected_code, "WORKFLOW")
    assert (runtime_result[0].value, runtime_result[1].value) == (expected_code, "WORKFLOW")


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


@pytest.mark.parametrize(
    ("builder", "context_factory", "definition_getter", "error_code"),
    [
        (
            build_callback_event,
            CallbackDiagnosticContext,
            get_callback_definition,
            CallbackErrorCode.INBOX_PROCESSING_TIMEOUT,
        ),
        (build_callback_event, CallbackDiagnosticContext, get_callback_definition, CallbackErrorCode.CONFIG_INVALID),
        (
            build_runtime_event,
            RuntimeDiagnosticContext,
            get_runtime_definition,
            RuntimeErrorCode.INBOX_PROCESSING_TIMEOUT,
        ),
        (build_runtime_event, RuntimeDiagnosticContext, get_runtime_definition, RuntimeErrorCode.CONFIG_INVALID),
    ],
)
def test_active_diagnostics_do_not_direct_operators_to_retired_plugin_runtime(
    builder,
    context_factory,
    definition_getter,
    error_code,
) -> None:
    event = builder(error_code=error_code, context=context_factory(), message="运行失败")
    definition = definition_getter(error_code)
    diagnostic_text = " ".join(
        [
            event.user_message or "",
            *event.next_steps,
            definition.cause,
            definition.operator_action,
            definition.fix,
        ]
    )

    assert "插件" not in diagnostic_text
    assert "plugin" not in diagnostic_text.lower()
