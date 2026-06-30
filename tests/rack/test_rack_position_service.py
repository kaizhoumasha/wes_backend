from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from src.app.resource.models import RackKind
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.app.runtime.orchestration.repositories.rack_position_repository import WorklineRackPositionRepository
from src.app.workline.services.rack_position_service import WorklineRackPositionService


class CapturingExecuteResult:
    def scalar_one_or_none(self) -> None:
        return None


class CapturingDb:
    def __init__(self) -> None:
        self.statement: Any | None = None

    async def execute(self, statement: Any) -> CapturingExecuteResult:
        self.statement = statement
        return CapturingExecuteResult()


class RecordingRackPositionRepo:
    def __init__(self, position: WorklineRackPosition | None) -> None:
        self.position = position
        self.lookups: list[tuple[str, str]] = []
        self.locked_lookups: list[tuple[str, str]] = []

    async def get_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> WorklineRackPosition | None:
        self.lookups.append((workline_code, position_code))
        return self.position

    async def get_by_workline_position_for_update(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> WorklineRackPosition | None:
        self.locked_lookups.append((workline_code, position_code))
        return self.position


def _position(**overrides: Any) -> WorklineRackPosition:
    values: dict[str, Any] = {
        "workline_id": 1001,
        "workline_code": "SMT_SORTER_01",
        "position_code": "SINGLE_LAYER_A",
        "position_name": "单层货架 A",
        "position_role": "SMT_SORTER_STATION",
        "allowed_rack_kind": RackKind.SINGLE_LAYER,
        "capacity": 1,
        "logic_location_code": "SMT_SORTER_01_SINGLE_A",
        "external_location_code": "RCS-SINGLE-A",
        "device_role": "OUTPUT_ARM",
        "priority": 10,
        "enabled": True,
    }
    values.update(overrides)
    return WorklineRackPosition(**values)


@pytest.mark.asyncio
async def test_get_by_workline_position_for_update_builds_for_update_query() -> None:
    db = CapturingDb()
    repository = WorklineRackPositionRepository()

    result = await repository.get_by_workline_position_for_update(
        db,  # type: ignore[arg-type]
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
    )

    assert result is None
    assert db.statement is not None
    compiled = str(db.statement.compile(dialect=postgresql.dialect())).upper()
    assert "FOR UPDATE" in compiled


@pytest.mark.asyncio
async def test_require_enabled_position_accepts_matching_rack_kind_with_capacity_two() -> None:
    repo = RecordingRackPositionRepo(_position(capacity=2))
    service = WorklineRackPositionService(repository=repo)

    result = await service.require_enabled_position(
        SimpleNamespace(),
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        rack_kind=RackKind.SINGLE_LAYER,
    )

    assert result.position_code == "SINGLE_LAYER_A"
    assert result.allowed_rack_kind == RackKind.SINGLE_LAYER
    assert result.capacity == 2
    assert repo.lookups == [("SMT_SORTER_01", "SINGLE_LAYER_A")]


@pytest.mark.asyncio
async def test_require_position_capacity_returns_enabled_capacity_two() -> None:
    service = WorklineRackPositionService(repository=RecordingRackPositionRepo(_position(capacity=2)))

    capacity = await service.require_position_capacity(
        SimpleNamespace(),
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
    )

    assert capacity == 2


@pytest.mark.asyncio
async def test_require_enabled_position_for_update_uses_locked_lookup() -> None:
    repo = RecordingRackPositionRepo(_position(capacity=2))
    service = WorklineRackPositionService(repository=repo)

    result = await service.require_enabled_position_for_update(
        SimpleNamespace(),
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        rack_kind=RackKind.SINGLE_LAYER,
    )

    assert result.capacity == 2
    assert repo.locked_lookups == [("SMT_SORTER_01", "SINGLE_LAYER_A")]
    assert repo.lookups == []


@pytest.mark.asyncio
async def test_require_position_capacity_for_update_returns_locked_position_and_capacity() -> None:
    repo = RecordingRackPositionRepo(_position(capacity=2))
    service = WorklineRackPositionService(repository=repo)

    position, capacity = await service.require_position_capacity_for_update(
        SimpleNamespace(),
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        rack_kind=RackKind.SINGLE_LAYER,
    )

    assert position.position_code == "SINGLE_LAYER_A"
    assert capacity == 2
    assert repo.locked_lookups == [("SMT_SORTER_01", "SINGLE_LAYER_A")]
    assert repo.lookups == []


@pytest.mark.asyncio
async def test_require_position_capacity_rejects_disabled_position() -> None:
    service = WorklineRackPositionService(repository=RecordingRackPositionRepo(_position(enabled=False)))

    with pytest.raises(ValueError, match="disabled"):
        await service.require_position_capacity(
            SimpleNamespace(),
            workline_code="SMT_SORTER_01",
            position_code="SINGLE_LAYER_A",
        )


@pytest.mark.asyncio
async def test_require_enabled_position_rejects_rack_kind_mismatch() -> None:
    service = WorklineRackPositionService(repository=RecordingRackPositionRepo(_position()))

    with pytest.raises(ValueError, match="allowed rack kind"):
        await service.require_enabled_position(
            SimpleNamespace(),
            workline_code="SMT_SORTER_01",
            position_code="SINGLE_LAYER_A",
            rack_kind=RackKind.FIVE_LAYER,
        )
