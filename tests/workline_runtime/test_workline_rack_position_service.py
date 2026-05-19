from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import RackKind
from src.app.workline.models.rack_position import WorklineRackPosition
from src.app.workline.services.rack_position_service import WorklineRackPositionService


class RecordingRackPositionRepo:
    def __init__(self, position: WorklineRackPosition | None) -> None:
        self.position = position
        self.lookups: list[tuple[str, str]] = []

    async def get_by_workline_position(
        self,
        _db: object,
        *,
        workline_code: str,
        position_code: str,
    ) -> WorklineRackPosition | None:
        self.lookups.append((workline_code, position_code))
        return self.position


def _position(**overrides: Any) -> WorklineRackPosition:
    values: dict[str, Any] = {
        "workline_id": 1001,
        "workline_code": "SMT_SORTER_01",
        "position_code": "SINGLE_LAYER_A",
        "position_name": "单层货架 A",
        "position_role": "OUTPUT_BUFFER",
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
async def test_require_enabled_position_accepts_matching_rack_kind() -> None:
    repo = RecordingRackPositionRepo(_position())
    service = WorklineRackPositionService(repository=repo)

    result = await service.require_enabled_position(
        SimpleNamespace(),
        workline_code="SMT_SORTER_01",
        position_code="SINGLE_LAYER_A",
        rack_kind=RackKind.SINGLE_LAYER,
    )

    assert result.position_code == "SINGLE_LAYER_A"
    assert result.allowed_rack_kind == RackKind.SINGLE_LAYER
    assert repo.lookups == [("SMT_SORTER_01", "SINGLE_LAYER_A")]


@pytest.mark.asyncio
async def test_require_enabled_position_rejects_disabled_position() -> None:
    service = WorklineRackPositionService(repository=RecordingRackPositionRepo(_position(enabled=False)))

    with pytest.raises(ValueError, match="disabled"):
        await service.require_enabled_position(
            SimpleNamespace(),
            workline_code="SMT_SORTER_01",
            position_code="SINGLE_LAYER_A",
            rack_kind=RackKind.SINGLE_LAYER,
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
