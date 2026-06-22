from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.workline_runtime.diagnostics import ErrorCode
from src.workline_runtime.orchestrator import OrchestratorService
from src.workline_runtime.runtime_intent import RuntimeIntent
from src.workline_runtime.services import WorklineRuntimeServices


class _NoopPlugin:
    contract_version = "rough_sorter.v2"

    async def on_device_event(self, ctx, inbox):
        return [RuntimeIntent.update_context({"contract_version_checked": True})]


@asynccontextmanager
async def _lock_provider(lock_key: str):
    _ = lock_key
    yield


def _orchestrator() -> OrchestratorService:
    return OrchestratorService(lock_provider=_lock_provider)


def _workline() -> SimpleNamespace:
    return SimpleNamespace(id=1, plugin_class=_NoopPlugin)


def _session(contract_version: str) -> SimpleNamespace:
    return SimpleNamespace(id=10, status="RUNNING", context_json={}, contract_version=contract_version)


@pytest.mark.asyncio
async def test_orchestrator_rejects_old_session_contract_after_plugin_contract_bump() -> None:
    result = await _orchestrator().process_inbox(
        session=_session("rough_sorter.v1"),
        workline=_workline(),
        inbox=SimpleNamespace(id=100, kind="DEVICE_EVENT"),
        devices_by_role={},
        services=WorklineRuntimeServices(),
        trace_id="trace-old-contract",
    )

    assert result.success is False
    assert result.error_code == ErrorCode.CONTRACT_MISMATCH.value
    assert "rough_sorter.v1" in (result.error or "")
    assert "rough_sorter.v2" in (result.error or "")


@pytest.mark.asyncio
async def test_orchestrator_accepts_migrated_session_contract_after_plugin_contract_bump() -> None:
    result = await _orchestrator().process_inbox(
        session=_session("rough_sorter.v2"),
        workline=_workline(),
        inbox=SimpleNamespace(id=100, kind="DEVICE_EVENT"),
        devices_by_role={},
        services=WorklineRuntimeServices(),
        trace_id="trace-new-contract",
    )

    assert result.success is True
    assert result.error_code is None
