"""Mandatory binding 与 SMT claim fail-closed 接线。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from src.app.runtime.orchestration.diagnostics import ErrorCode
from src.app.workline.services.plugin_binding_service import (
    PluginBindingAdmissionError,
    WorklinePluginBindingService,
)
from tests.workline_runtime.extensions.test_plugin_binding_runtime_wiring import (
    DEFINITION,
    BindingRepository,
    RuntimeRepository,
    _binding,
    _workline,
)

if TYPE_CHECKING:
    from src.app.workline.models import WorkLine


@pytest.mark.asyncio
async def test_runtime_session_pin_rejects_missing_resolved_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    binding_service = WorklinePluginBindingService(
        repository=BindingRepository(),
        runtime_repository=RuntimeRepository(),
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="b" * 64,
    )

    async def _missing_binding(_db: object, *, binding_id: int) -> None:
        assert binding_id == 8

    monkeypatch.setattr(binding_service, "get_pinned", _missing_binding)

    with pytest.raises(PluginBindingAdmissionError, match=r"^PLUGIN_BINDING_REQUIRED$"):
        await binding_service.pin_new_runtime_session(object(), workline=_workline(), session=SimpleNamespace())


def test_plugin_binding_required_uses_one_exported_reason_code() -> None:
    plugin_binding_module = importlib.import_module("src.app.workline.services.plugin_binding_service")

    assert not hasattr(plugin_binding_module, "PLUGIN_BINDING_REQUIRED")
    assert ErrorCode.PLUGIN_BINDING_REQUIRED.value == "PLUGIN_BINDING_REQUIRED"


@pytest.mark.asyncio
async def test_new_session_binding_rejects_workline_without_active_binding() -> None:
    binding_service = WorklinePluginBindingService(
        repository=BindingRepository(),
        runtime_repository=RuntimeRepository(),
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="b" * 64,
    )
    workline = _workline()
    workline.active_plugin_binding_id = None

    with pytest.raises(PluginBindingAdmissionError, match=r"^PLUGIN_BINDING_REQUIRED$"):
        await binding_service.resolve_new_session_binding(object(), workline=workline)


@pytest.mark.asyncio
async def test_runtime_session_pin_never_returns_none_for_unbound_workline() -> None:
    binding_service = WorklinePluginBindingService(
        repository=BindingRepository(),
        runtime_repository=RuntimeRepository(),
        plugin_index={("platform-test", "v1"): DEFINITION},
        capability_index={},
        plugin_index_digest="b" * 64,
    )
    workline = _workline()
    workline.active_plugin_binding_id = None

    with pytest.raises(PluginBindingAdmissionError, match=r"^PLUGIN_BINDING_REQUIRED$"):
        await binding_service.pin_new_runtime_session(object(), workline=workline, session=SimpleNamespace())


@pytest.mark.asyncio
async def test_smt_claim_resolves_active_binding_before_creating_session_aggregate() -> None:
    from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
    from src.utils.timezone import timezone

    events: list[tuple[str, object]] = []
    binding = _binding()
    target_workline = _workline()

    class Repository:
        async def lock_target_workline_by_id(self, _db: object, *, workline_id: int) -> WorkLine:
            assert workline_id == target_workline.id
            return target_workline

    class MandatoryBindingService:
        async def resolve_new_session_binding(self, _db: object, *, workline: WorkLine) -> SimpleNamespace:
            events.append(("resolve", workline))
            return binding

    class Service(SmtInboundHandoffService):
        async def _lock_claimable_demand_and_ready_candidate_or_retry(
            self,
            _db: object,
            *,
            candidate: object,
            now: object,
        ) -> tuple[object, object, None]:
            _ = now
            return demand, candidate, None

        async def _target_has_open_current_material(self, _db: object, *, workline_id: int) -> bool:
            _ = workline_id
            return False

        async def _target_has_in_flight_handoff_source_item(
            self,
            _db: object,
            *,
            workline_id: int,
            source_item_id: int,
        ) -> bool:
            _ = (workline_id, source_item_id)
            return False

        async def _create_sorting_claim_session(
            self,
            _db: object,
            *,
            binding: object | None = None,
            **_kwargs: object,
        ) -> SimpleNamespace:
            events.append(("create", binding))
            return SimpleNamespace(
                session=SimpleNamespace(id=21),
                execution_session_id=22,
                correlation_id="workline-session:SMT-CLAIM-21",
            )

        async def _create_source_pick_request_inbox(self, _db: object, **kwargs: object) -> SimpleNamespace:
            assert kwargs["execution_session_id"] == 22
            assert kwargs["correlation_id"] == "workline-session:SMT-CLAIM-21"
            return SimpleNamespace(id=31)

        async def recalculate_demand_status(self, _db: object, _demand: object, *, reason: str) -> object:
            _ = reason
            return demand

    class Db:
        def add(self, _record: object) -> None:
            return None

    demand = SimpleNamespace(
        id=11,
        demand_key="smt-demand-11",
        trace_id="trace-11",
        target_workline_id=None,
        target_workline_code=None,
        failure_code=None,
        failure_message=None,
    )
    item = SimpleNamespace(
        id=12,
        claim_attempt_no=1,
        status="READY",
        target_workline_id=None,
        target_workline_code=None,
        sorting_session_id=None,
        source_pick_inbox_id=None,
        claimed_at=None,
        failure_code=None,
        failure_message=None,
        next_attempt_at=None,
    )
    service = Service(repository=Repository())
    service.plugin_binding_service = MandatoryBindingService()
    now = timezone.now_for_db()

    result = await service._claim_selected_route_source_item(
        Db(),
        candidate=item,
        route=SimpleNamespace(route_evidence={}),
        workline_id=7,
        workline_code="PLATFORM-01",
        now=now,
        route_probe_started_at=now,
        trace_id="trace-11",
    )

    assert result.kind == "CLAIMED"
    assert events == [("resolve", target_workline), ("create", binding)]
