"""统一 PositionProjection 以冻结 execution authority 守住 current-only 事实。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from src.app.execution.services.position_projection_service import (
    PositionProjectionAuthorityError,
    PositionProjectionService,
)
from src.app.transport.contracts import TransportExecutionAuthority

if TYPE_CHECKING:
    from src.app.execution.models.position_projection import PositionProjection


class _Repository:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.projection = None
        self.epoch = SimpleNamespace(id=11, workline_id=7, status="ACTIVE")
        self.bin_execution = SimpleNamespace(
            id=31,
            bin_id="BIN-001",
            workline_id=7,
            line_run_epoch_id=11,
            status="ACTIVE",
        )

    async def lock_epoch_lifecycle(self, _db: object, line_run_epoch_id: int) -> None:
        self.calls.append(f"epoch:{line_run_epoch_id}")

    async def get_epoch_for_update(self, _db: object, line_run_epoch_id: int):
        self.calls.append(f"epoch-row:{line_run_epoch_id}")
        return self.epoch

    async def get_bin_execution_for_update(self, _db: object, bin_execution_id: int):
        self.calls.append(f"bin-row:{bin_execution_id}")
        return self.bin_execution

    async def lock_projection(self, _db: object, object_type: str, object_id: str) -> None:
        self.calls.append(f"projection-lock:{object_type}:{object_id}")

    async def get_for_update(self, _db: object, object_type: str, object_id: str):
        self.calls.append(f"projection-row:{object_type}:{object_id}")
        return self.projection

    async def add(self, _db: object, projection: PositionProjection) -> PositionProjection:
        projection.id = 41
        self.projection = projection
        self.calls.append("add")
        return projection

    async def flush(self, _db: object) -> None:
        self.calls.append("flush")


@pytest.mark.asyncio
async def test_rack_projection_uses_epoch_then_projection_lock_and_frozen_authority() -> None:
    repository = _Repository()
    service = PositionProjectionService(repository=repository)
    authority = TransportExecutionAuthority(workline_id=7, line_run_epoch_id=11)

    projection = await service.apply_transport_result(
        object(),
        authority=authority,
        object_type="RACK",
        object_id="RACK-001",
        position={"kind": "RACK_POSITION", "location_code": "R1"},
        position_unknown=False,
        arrival_face="A",
        operation_id="019d0000-0000-7000-8000-000000000001",
        transport_task_id="transport-1",
        updated_at=datetime(2026, 8, 28),
    )

    assert projection.workline_id == 7
    assert projection.line_run_epoch_id == 11
    assert projection.bin_execution_id is None
    assert repository.calls == [
        "epoch:11",
        "epoch-row:11",
        "projection-lock:RACK:RACK-001",
        "projection-row:RACK:RACK-001",
        "epoch-row:11",
        "add",
        "flush",
    ]


@pytest.mark.asyncio
async def test_bin_projection_requires_matching_active_bin_execution() -> None:
    repository = _Repository()
    service = PositionProjectionService(repository=repository)

    with pytest.raises(PositionProjectionAuthorityError):
        await service.apply_transport_result(
            object(),
            authority=TransportExecutionAuthority(workline_id=7, line_run_epoch_id=11, bin_execution_id=31),
            object_type="BIN",
            object_id="BIN-OTHER",
            position={"kind": "HANDOFF_POSITION", "location_code": "H1"},
            position_unknown=False,
            arrival_face=None,
            operation_id="019d0000-0000-7000-8000-000000000002",
            transport_task_id="transport-2",
            updated_at=datetime(2026, 8, 28),
        )

    assert repository.projection is None
    assert repository.calls == ["epoch:11", "epoch-row:11", "bin-row:31"]


@pytest.mark.asyncio
async def test_missing_authority_is_a_noop_for_debug_transport() -> None:
    repository = _Repository()
    service = PositionProjectionService(repository=repository)

    projection = await service.apply_transport_result(
        object(),
        authority=None,
        object_type="RACK",
        object_id="RACK-DEBUG",
        position={"kind": "RACK_POSITION", "location_code": "DEBUG"},
        position_unknown=False,
        arrival_face="A",
        operation_id="019d0000-0000-7000-8000-000000000003",
        transport_task_id="transport-debug",
        updated_at=datetime(2026, 8, 28),
    )

    assert projection is None
    assert repository.calls == []
