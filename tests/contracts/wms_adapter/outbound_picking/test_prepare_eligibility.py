from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app.wms_integration.outbound_picking.repositories import PickingWorklineEligibilityRepository


class _Db:
    def __init__(self, *scalar_results: object) -> None:
        self.results = iter(scalar_results)

    async def scalar(self, _statement: object) -> object:
        return next(self.results)


class _Epochs:
    def __init__(self, *, bindings: list[object] | None = None, positions: list[object] | None = None) -> None:
        self.bindings = bindings if bindings is not None else [_binding()]
        self.positions = positions if positions is not None else [SimpleNamespace(position_role="POINT2")]

    async def list_bindings(self, _db: object, _epoch_id: int) -> list[object]:
        return self.bindings

    async def list_position_bindings(self, _db: object, _epoch_id: int) -> list[object]:
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
async def test_eligibility_requires_bindings_fresh_idle_devices_and_clear_positions() -> None:
    repository = PickingWorklineEligibilityRepository(
        epoch_repository=_Epochs(),  # type: ignore[arg-type]
        observation_repository=_Observations(),  # type: ignore[arg-type]
    )

    assert await repository.is_ready(
        _Db(False, False),  # type: ignore[arg-type]
        workline_id=7,
        line_run_epoch_id=21,
        now=datetime(2026, 9, 4),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observation",
    [
        None,
        _observation(contract_key="other"),
        _observation(contract_version="2.0"),
        _observation(received_at=datetime(2026, 9, 4) - timedelta(seconds=6)),
        _observation(mode="MANUAL"),
        _observation(status="BUSY"),
        _observation(current_command_code="CMD-1"),
    ],
)
async def test_eligibility_fails_closed_for_missing_or_invalid_device_fact(observation: object | None) -> None:
    observations = _Observations()
    observations.observation = observation
    repository = PickingWorklineEligibilityRepository(
        epoch_repository=_Epochs(),  # type: ignore[arg-type]
        observation_repository=observations,  # type: ignore[arg-type]
    )

    assert not await repository.is_ready(
        _Db(False),  # type: ignore[arg-type]
        workline_id=7,
        line_run_epoch_id=21,
        now=datetime(2026, 9, 4),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("db", "epochs"),
    [
        (_Db(True), _Epochs()),
        (_Db(False), _Epochs(bindings=[])),
        (_Db(False), _Epochs(positions=[])),
        (_Db(False, True), _Epochs()),
    ],
)
async def test_eligibility_fails_closed_for_safety_missing_topology_or_positioned_object(
    db: _Db, epochs: _Epochs
) -> None:
    repository = PickingWorklineEligibilityRepository(
        epoch_repository=epochs,  # type: ignore[arg-type]
        observation_repository=_Observations(),  # type: ignore[arg-type]
    )

    assert not await repository.is_ready(
        db,  # type: ignore[arg-type]
        workline_id=7,
        line_run_epoch_id=21,
        now=datetime(2026, 9, 4),
    )
