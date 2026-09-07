"""WorkLine 准入先于动作；物理位置按箱码保留，不依赖箱执行实体。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.app.execution.services.position_projection_service import (
    PositionProjectionAuthorityError,
    PositionProjectionService,
)
from src.app.transport.contracts import TransportExecutionAuthority


class Repository:
    def __init__(self):
        self.calls = []
        self.line = SimpleNamespace(id=7, is_active=True)
        self.projection = None
        self.other_line_occupied = False

    async def get_workline_for_update(self, db, workline_id):
        self.calls.append("workline")
        return self.line

    async def lock_projection(self, db, object_type, object_id):
        self.calls.append("object")

    async def get_for_update(self, db, object_type, object_id):
        return self.projection

    async def is_workline_position(self, db, workline_id, position):
        return self.other_line_occupied

    async def add(self, db, projection):
        self.projection = projection
        return projection

    async def flush(self, db):
        self.calls.append("flush")


async def apply(repo, *, object_id="BIN-001", authority=TransportExecutionAuthority(workline_id=7)):
    return await PositionProjectionService(repository=repo).apply_transport_result(
        object(),
        authority=authority,
        object_type="BIN",
        object_id=object_id,
        position={"kind": "HANDOFF_POSITION", "location_code": "OUTSIDE"},
        position_unknown=False,
        arrival_face=None,
        operation_id="operation-1",
        transport_task_id="transport-1",
        updated_at=datetime(2026, 9, 6),
    )


@pytest.mark.asyncio
async def test_bin_code_gets_retained_position_without_bin_execution():
    repo = Repository()
    projection = await apply(repo)
    assert projection.object_id == "BIN-001"
    assert projection.workline_id == 7
    assert projection.position_json["location_code"] == "OUTSIDE"
    assert repo.calls == ["workline", "object", "flush"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown,occupied", [(True, False), (False, True)])
async def test_foreign_unknown_or_occupied_position_cannot_be_overwritten(unknown, occupied):
    repo = Repository()
    repo.projection = SimpleNamespace(
        workline_id=8, position_unknown=unknown, position_json={"location_code": "FOREIGN"}
    )
    repo.other_line_occupied = occupied
    with pytest.raises(PositionProjectionAuthorityError, match="another WorkLine"):
        await apply(repo)
    assert repo.projection.workline_id == 8
    assert "flush" not in repo.calls


@pytest.mark.asyncio
async def test_known_outside_position_can_enter_new_workline():
    repo = Repository()
    repo.projection = SimpleNamespace(workline_id=8, position_unknown=False, position_json={"location_code": "OUTSIDE"})
    projection = await apply(repo)
    assert projection.workline_id == 7


@pytest.mark.asyncio
async def test_stopped_workline_is_rejected_before_object_write():
    repo = Repository()
    repo.line.is_active = False
    with pytest.raises(PositionProjectionAuthorityError, match="active WorkLine"):
        await apply(repo)
    assert repo.calls == ["workline"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unknown,source",
    [
        (True, {"kind": "HANDOFF_POSITION", "location_code": "P1"}),
        (False, {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}),
    ],
)
async def test_new_transport_checks_unknown_and_source_before_obligation(unknown, source):
    repo = Repository()
    repo.projection = SimpleNamespace(
        workline_id=7, position_unknown=unknown, position_json={"kind": "HANDOFF_POSITION", "location_code": "P1"}
    )
    with pytest.raises(PositionProjectionAuthorityError):
        await PositionProjectionService(repository=repo).admit_transport_member(
            object(),
            authority=TransportExecutionAuthority(workline_id=7),
            object_type="BIN",
            object_id="BIN-001",
            source=source,
        )
    assert "flush" not in repo.calls


@pytest.mark.asyncio
async def test_debug_result_without_authority_is_noop():
    repo = Repository()
    assert await apply(repo, authority=None) is None
    assert repo.calls == []
