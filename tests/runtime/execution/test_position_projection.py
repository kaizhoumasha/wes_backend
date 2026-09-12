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
    assert repo.calls == ["object", "workline", "flush"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown,occupied", [(True, False), (False, True)])
async def test_unordered_other_task_retains_position_and_marks_it_unconfirmed(unknown, occupied):
    repo = Repository()
    repo.projection = SimpleNamespace(
        workline_id=8,
        position_unknown=unknown,
        position_json={"location_code": "FOREIGN"},
        source_transport_task_id="transport-new",
        source_operation_id="new-result",
        arrival_face=None,
        updated_at=datetime(2026, 9, 5),
    )
    repo.other_line_occupied = occupied
    await apply(repo)
    assert repo.projection.workline_id == 8
    assert repo.projection.position_unknown is True
    assert repo.projection.position_json == {"location_code": "FOREIGN"}
    assert repo.projection.source_transport_task_id == "transport-new"
    assert repo.projection.source_operation_id == "new-result"
    assert repo.projection.updated_at == datetime(2026, 9, 5)


@pytest.mark.asyncio
async def test_same_task_result_can_refine_unknown_position():
    repo = Repository()
    repo.projection = SimpleNamespace(
        workline_id=7, position_unknown=True, position_json=None, source_transport_task_id="transport-1"
    )
    projection = await apply(repo)
    assert projection.workline_id == 7
    assert projection.position_unknown is False


@pytest.mark.asyncio
async def test_stopped_workline_does_not_block_retention_of_original_task_result():
    repo = Repository()
    repo.line.is_active = False
    projection = await apply(repo)
    assert projection.position_json["location_code"] == "OUTSIDE"
    assert repo.calls == ["object", "workline", "flush"]


@pytest.mark.asyncio
async def test_missing_workline_keeps_task_fact_without_creating_invalid_projection_owner():
    repo = Repository()
    repo.line = None
    assert await apply(repo) is None
    assert repo.projection is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unknown,source",
    [
        (True, {"kind": "HANDOFF_POSITION", "location_code": "P1"}),
        (False, {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}),
    ],
)
@pytest.mark.parametrize("workline_id", [7, 8])
async def test_new_transport_ignores_stale_or_unknown_projection(unknown, source, workline_id):
    repo = Repository()
    repo.projection = SimpleNamespace(
        workline_id=workline_id,
        position_unknown=unknown,
        position_json={"kind": "HANDOFF_POSITION", "location_code": "P1"},
    )
    await PositionProjectionService(repository=repo).admit_transport_member(
        object(),
        authority=TransportExecutionAuthority(workline_id=7),
        object_type="BIN",
    )
    assert repo.projection.workline_id == workline_id
    assert repo.projection.position_unknown == unknown
    assert "flush" not in repo.calls


@pytest.mark.asyncio
async def test_debug_result_without_authority_is_noop():
    repo = Repository()
    assert await apply(repo, authority=None) is None
    assert repo.calls == []


@pytest.mark.asyncio
async def test_explicitly_stopped_workline_still_rejects_new_transport():
    repo = Repository()
    repo.line.is_active = False
    with pytest.raises(PositionProjectionAuthorityError, match="active WorkLine"):
        await PositionProjectionService(repository=repo).admit_transport_member(
            object(), authority=TransportExecutionAuthority(workline_id=7), object_type="RACK"
        )
    assert repo.calls == ["workline"]
