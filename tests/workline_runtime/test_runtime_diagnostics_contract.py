"""Runtime 诊断单一真值与强制 binding 契约。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.diagnostics import (
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    Recoverability,
    Severity,
    build_diagnostic_context,
    build_diagnostic_event,
    error_domain_for,
    get_diagnostic_code_definition,
)
from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_context_loader
from src.app.runtime.orchestration.services.trace.trace_query_service import _SESSION_FAILURE_CODE_MAP
from src.app.workline.services.plugin_binding_service import PluginBindingAdmissionError


def test_plugin_binding_required_is_registered_as_one_runtime_diagnostic_truth() -> None:
    member = ErrorCode.__members__.get("PLUGIN_BINDING_REQUIRED")

    assert member is not None
    assert member.value == "PLUGIN_BINDING_REQUIRED"
    assert error_domain_for(member) is ErrorDomain.CONFIG
    definition = get_diagnostic_code_definition(member)
    assert definition.code is member
    assert definition.recoverability is Recoverability.MANUAL_INTERVENTION_REQUIRED
    event = build_diagnostic_event(
        error_code=member,
        context=build_diagnostic_context(),
        message="binding required",
    )
    assert event.error_domain is ErrorDomain.CONFIG
    assert event.severity is Severity.ERROR
    assert event.problem_class is ProblemClass.SOFTWARE
    assert _SESSION_FAILURE_CODE_MAP["PLUGIN_BINDING_REQUIRED"] is member


def test_plugin_binding_service_uses_error_code_instead_of_module_string_constant() -> None:
    from src.app.workline.services import plugin_binding_service

    source = inspect.getsource(plugin_binding_service)

    assert "PLUGIN_BINDING_REQUIRED =" not in source
    assert "ErrorCode.PLUGIN_BINDING_REQUIRED.value" in source


@pytest.mark.asyncio
async def test_runtime_inbox_rejects_session_without_pinned_plugin_binding() -> None:
    workline = SimpleNamespace(active_plugin_binding_id=None)
    session = SimpleNamespace(plugin_binding_id=None)

    with pytest.raises(PluginBindingAdmissionError, match=r"^PLUGIN_BINDING_REQUIRED$"):
        await runtime_inbox_context_loader._assert_platform_plugin_binding_admitted(
            object(),
            workline=workline,
            session=session,
            devices_by_role={},
        )
