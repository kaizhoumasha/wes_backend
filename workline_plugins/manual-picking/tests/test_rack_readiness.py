"""FIVE_RACK 单一 current 约束属于插件，不改变基础位置 capacity。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from manual_picking.application.rack_readiness import rack_ready, ready_rack_projection
from test_batch_repository import _new_sessions

from src.app.execution.models import PositionProjection
from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("current_count", [0, 1, 2])
@pytest.mark.parametrize("reader", [rack_ready, ready_rack_projection])
async def test_five_rack_readiness_requires_exactly_one_current_rack(current_count, reader):
    engine, sessions = await _new_sessions()
    line = SimpleNamespace(id=7, position_bindings={"FIVE_RACK": {"location_id": "KT16"}})
    positions = PositionProjectionRepository()
    transports = SimpleNamespace(get_task=AsyncMock(return_value=SimpleNamespace(status="SUCCEEDED")))
    try:
        async with sessions.begin() as db:
            for index in range(current_count):
                db.add(
                    PositionProjection(
                        object_type="RACK",
                        object_id=f"R{index}",
                        workline_id=7,
                        position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                        arrival_face="90",
                        source_operation_id=f"arrival-{index}",
                        source_transport_task_id=f"arrival-{index}",
                    )
                )
            # 其他线、其他位置或 BIN 不能误计为本线同位 current rack。
            for kind, identity, line_id, location in (
                ("RACK", "OTHER-LINE", 8, "KT16"),
                ("RACK", "OTHER-POS", 7, "KT17"),
                ("BIN", "B1", 7, "KT16"),
            ):
                db.add(
                    PositionProjection(
                        object_type=kind,
                        object_id=identity,
                        workline_id=line_id,
                        position_json={"kind": "RACK_POSITION", "location_code": location},
                        arrival_face="90",
                        source_operation_id=identity,
                        source_transport_task_id=identity,
                    )
                )
            await db.flush()
            result = await reader(db, line, "R0", "90", "KT16", positions=positions, transports=transports)
            assert bool(result) is (current_count == 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_five_rack_old_unknown_projection_does_not_block_new_known_rack():
    engine, sessions = await _new_sessions()
    line = SimpleNamespace(id=7, position_bindings={"FIVE_RACK": {"location_id": "KT16"}})
    try:
        async with sessions.begin() as db:
            for index in range(2):
                db.add(
                    PositionProjection(
                        object_type="RACK",
                        object_id=f"R{index}",
                        workline_id=7,
                        position_json={"kind": "RACK_POSITION", "location_code": "KT16"},
                        arrival_face="90",
                        position_unknown=bool(index),
                        source_operation_id=f"arrival-{index}",
                        source_transport_task_id=f"arrival-{index}",
                    )
                )
            await db.flush()
            assert await rack_ready(
                db,
                line,
                "R0",
                "90",
                "KT16",
                positions=PositionProjectionRepository(),
                transports=SimpleNamespace(get_task=AsyncMock(return_value=SimpleNamespace(status="SUCCEEDED"))),
            )
    finally:
        await engine.dispose()
