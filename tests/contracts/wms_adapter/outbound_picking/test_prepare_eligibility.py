from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository
from src.app.wms_integration.outbound_picking.repositories import PickingWorklineFactsRepository


class _Worklines:
    def __init__(self, *, bindings: list[object] | None = None, positions: list[object] | None = None) -> None:
        self.bindings = bindings if bindings is not None else [_binding()]
        self.positions = positions if positions is not None else [SimpleNamespace(position_role="POINT2")]

    async def list_bindings(self, _db: object, _workline_id: int) -> list[object]:
        return self.bindings

    async def list_position_bindings(self, _db: object, _workline_id: int) -> list[object]:
        return self.positions


class _Observations:
    def __init__(self, observation: object | None = None) -> None:
        self.observation = observation if observation is not None else _observation()

    async def get_latest_for_device(self, _db: object, _device_code: str) -> object | None:
        return self.observation


def _binding() -> object:
    return SimpleNamespace(
        device_code="DEVICE-1",
        contract_key="manual.conveyor",
        contract_version="1.0",
        status_max_age_ms=5_000,
    )


def _observation(**changes: object) -> object:
    values = {
        "contract_key": "manual.conveyor",
        "contract_version": "1.0",
        "received_at": datetime(2026, 9, 4),
        "mode": "AUTO",
        "status": "IDLE",
        "current_command_code": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_repository_returns_immutable_facts_without_applying_business_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        PositionProjectionRepository, "get_active_workline_summary", AsyncMock(return_value={"count": 1})
    )
    repository = PickingWorklineFactsRepository(
        workline_repository=_Worklines(),  # type: ignore[arg-type]
        observation_repository=_Observations(_observation(mode="MANUAL")),  # type: ignore[arg-type]
    )
    facts = await repository.read_facts(
        object(),  # type: ignore[arg-type]
        workline_id=7,
    )
    assert facts.has_position_bindings is True
    assert facts.has_positioned_object is True
    assert isinstance(facts.devices, tuple)
    assert len(facts.devices) == 1
    assert facts.devices[0].mode == "MANUAL"
    assert facts.devices[0].contract_key == "manual.conveyor"
    assert facts.devices[0].received_at == datetime(2026, 9, 4)


@pytest.mark.asyncio
async def test_repository_preserves_missing_device_observation(monkeypatch) -> None:
    monkeypatch.setattr(
        PositionProjectionRepository, "get_active_workline_summary", AsyncMock(return_value={"count": 0})
    )
    observations = _Observations()
    observations.observation = None
    repository = PickingWorklineFactsRepository(
        workline_repository=_Worklines(),  # type: ignore[arg-type]
        observation_repository=observations,  # type: ignore[arg-type]
    )
    facts = await repository.read_facts(
        object(),  # type: ignore[arg-type]
        workline_id=7,
    )
    assert facts.has_positioned_object is False
    assert facts.devices[0].observed_contract_key is None
    assert facts.devices[0].received_at is None
