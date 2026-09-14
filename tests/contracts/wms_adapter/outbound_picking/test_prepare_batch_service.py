from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.wms_integration.outbound_picking.services import picking_task_prepare_batch as module


class _Sessions:
    def __init__(self) -> None:
        self.calls = 0

    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        yield object()


class _Worklines:
    def __init__(self, rows: list[tuple[int, str, str]]) -> None:
        self.rows = rows
        self.identities: tuple[tuple[str, str], ...] | None = None

    async def list_active_for_plugin_identities(
        self,
        _db: object,
        identities: tuple[tuple[str, str], ...],
        *,
        limit: int,
    ) -> list[tuple[int, str, str]]:
        assert limit == 100
        self.identities = identities
        return self.rows


class _Coordinator:
    def __init__(self, policy: object, calls: list[tuple[object, int]]) -> None:
        self._policy = policy
        self._calls = calls

    async def prepare_next_for_workline(self, workline_id: int):  # type: ignore[no-untyped-def]
        self._calls.append((self._policy, workline_id))
        return SimpleNamespace(prepared=workline_id == 7)


@pytest.mark.asyncio
async def test_batch_does_not_read_business_state_without_prepare_capability() -> None:
    sessions = _Sessions()
    service = module.PickingTaskPrepareBatchService(
        sessions,  # type: ignore[arg-type]
        plugins=(SimpleNamespace(plugin_key="other", plugin_version="1.0", picking_task_prepare_policy=None),),
        task_queue_gateway=SimpleNamespace(),  # type: ignore[arg-type]
        workline_repository=_Worklines([]),  # type: ignore[arg-type]
    )

    assert await service.prepare_batch() == 0
    assert sessions.calls == 0


@pytest.mark.asyncio
async def test_batch_routes_only_active_exact_plugin_versions_to_their_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manual_policy = object()
    manual_blocker = object()
    ignored_policy = object()
    plugins = (
        SimpleNamespace(
            plugin_key="sample_plugin",
            plugin_version="0.1.0",
            picking_task_prepare_policy=manual_policy,
            business_blocker=manual_blocker,
        ),
        SimpleNamespace(plugin_key="inactive", plugin_version="1.0", picking_task_prepare_policy=ignored_policy),
    )
    worklines = _Worklines([(7, "sample_plugin", "0.1.0"), (8, "sample_plugin", "0.1.0")])
    calls: list[tuple[object, int]] = []
    reserved = AsyncMock(return_value=False)
    reservations: list[object] = []
    blockers: list[object] = []

    def coordinator_factory(_sessions, *, policy, **_kwargs):  # type: ignore[no-untyped-def]
        reservations.append(_kwargs["workline_reserved"])
        blockers.append(_kwargs["business_blocker"])
        return _Coordinator(policy, calls)

    monkeypatch.setattr(module, "PickingTaskPrepareCoordinator", coordinator_factory)
    service = module.PickingTaskPrepareBatchService(
        _Sessions(),  # type: ignore[arg-type]
        plugins=plugins,  # type: ignore[arg-type]
        task_queue_gateway=SimpleNamespace(),  # type: ignore[arg-type]
        workline_repository=worklines,  # type: ignore[arg-type]
        workline_reserved=reserved,
    )

    assert await service.prepare_batch() == 1
    assert worklines.identities == (("inactive", "1.0"), ("sample_plugin", "0.1.0"))
    assert calls == [(manual_policy, 7), (manual_policy, 8)]
    assert reservations == [reserved]
    assert blockers == [manual_blocker]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 99, 101, True])
async def test_batch_rejects_non_fixed_limit(limit: object) -> None:
    service = module.PickingTaskPrepareBatchService(
        _Sessions(),  # type: ignore[arg-type]
        plugins=(),
        task_queue_gateway=SimpleNamespace(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="prepare batch limit must be 100"):
        await service.prepare_batch(limit=limit)  # type: ignore[arg-type]
