"""Runtime 诊断单一真值与强制 binding 契约。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import select

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
from src.app.runtime.orchestration.models.diagnostic import WorklineDiagnostic
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_context_loader
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
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


@pytest.mark.asyncio
async def test_missing_binding_error_carries_typed_error_code() -> None:
    workline = SimpleNamespace(active_plugin_binding_id=None)
    session = SimpleNamespace(plugin_binding_id=None)

    with pytest.raises(PluginBindingAdmissionError) as caught:
        await runtime_inbox_context_loader._assert_platform_plugin_binding_admitted(
            object(),
            workline=workline,
            session=session,
            devices_by_role={},
        )

    assert caught.value.error_code is ErrorCode.PLUGIN_BINDING_REQUIRED


@pytest.mark.asyncio
async def test_process_claimed_persists_missing_binding_diagnostic_and_dead_letter(
    db_session: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = RuntimeInbox(
        kind="INTERNAL_EVENT",
        workline_id=7,
        provider_code="WES_INTERNAL",
        event_type="TEST_EVENT",
        source_event_id="missing-binding-claimed",
        payload_hash="missing-binding-hash",
        payload_json={"event_type": "TEST_EVENT"},
        payload_schema_version=1,
        status="PROCESSING",
        claim_bucket_key="workline:7",
        processor_token="binding-worker",
        received_at=1,
    )
    db_session.add(inbox)
    await db_session.commit()
    bridge = RuntimeInboxProcessorBridge()

    async def _raise_missing_binding(*_args: object, **_kwargs: object) -> None:
        raise PluginBindingAdmissionError(
            ErrorCode.PLUGIN_BINDING_REQUIRED.value,
            error_code=ErrorCode.PLUGIN_BINDING_REQUIRED,
        )

    monkeypatch.setattr(bridge, "_process_claimed_core", _raise_missing_binding)

    result = await bridge.process_claimed(
        db_session,
        claim={"id": inbox.id, "processor_token": "binding-worker"},
    )

    await db_session.refresh(inbox)
    diagnostic = await db_session.scalar(
        select(WorklineDiagnostic).where(
            WorklineDiagnostic.inbox_id == inbox.id,
            WorklineDiagnostic.diagnostic_code == ErrorCode.PLUGIN_BINDING_REQUIRED.value,
        )
    )
    assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}
    assert inbox.status == "DEAD_LETTER"
    assert inbox.last_error_code == ErrorCode.PLUGIN_BINDING_REQUIRED.value
    assert diagnostic is not None
    assert diagnostic.recoverability == "manual_intervention_required"
