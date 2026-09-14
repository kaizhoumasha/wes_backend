"""货架到位只认当前投影与原 Transport 的确定终态。"""

from __future__ import annotations

from typing import Any


async def rack_ready(
    db: Any, line: Any, rack_id: str, face: str, location: str, *, positions: Any, transports: Any
) -> bool:
    projection = await positions.get(db, "RACK", rack_id)
    if (
        projection is None
        or projection.workline_id != line.id
        or projection.position_unknown
        or projection.position_json != {"kind": "RACK_POSITION", "location_code": location}
        or projection.arrival_face != face
        or not projection.source_transport_task_id
    ):
        return False
    transport = await transports.get_task(db, projection.source_transport_task_id)
    return transport is not None and transport.status == "SUCCEEDED"


__all__ = ["rack_ready"]
