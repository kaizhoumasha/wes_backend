"""货架到位只认当前投影与原 Transport 的确定终态。"""

from __future__ import annotations

from typing import Any

from manual_picking.definition import FIVE_RACK
from src.app.execution.models import PositionProjection


async def has_single_current_rack(db: Any, workline_id: int, location: str, *, positions: Any) -> bool:
    """FIVE_RACK 作业位只允许一个位置明确的 current rack；调用方持有工作线锁。"""
    columns = PositionProjection.__table__.c
    return (
        await positions.count(
            db,
            where_clauses=[
                columns.workline_id == workline_id,
                columns.object_type == "RACK",
                columns.position_unknown.is_(False),
                columns.position_json["kind"].as_string() == "RACK_POSITION",
                columns.position_json["location_code"].as_string() == location,
            ],
        )
        == 1
    )


async def ready_rack_projection(
    db: Any,
    line: Any,
    rack_id: str,
    face: str,
    location: str,
    *,
    positions: Any,
    transports: Any,
    source_cardinality_checked: bool = False,
) -> Any | None:
    projection = await positions.get(db, "RACK", rack_id)
    if (
        projection is None
        or projection.workline_id != line.id
        or projection.position_unknown
        or projection.position_json != {"kind": "RACK_POSITION", "location_code": location}
        or projection.arrival_face != face
        or not projection.source_transport_task_id
    ):
        return None
    if (
        not source_cardinality_checked
        and location == line.position_bindings.get(FIVE_RACK.slot_key, {}).get("location_id")
        and not await has_single_current_rack(db, line.id, location, positions=positions)
    ):
        return None
    transport = await transports.get_task(db, projection.source_transport_task_id)
    return projection if transport is not None and transport.status == "SUCCEEDED" else None


async def rack_ready(
    db: Any, line: Any, rack_id: str, face: str, location: str, *, positions: Any, transports: Any
) -> bool:
    return (
        await ready_rack_projection(
            db,
            line,
            rack_id,
            face,
            location,
            positions=positions,
            transports=transports,
        )
        is not None
    )


__all__ = ["has_single_current_rack", "rack_ready", "ready_rack_projection"]
