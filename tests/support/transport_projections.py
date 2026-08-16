"""Transport 核心测试使用的可靠货架工作面事实。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.transport.models import TransportPositionProjection
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.transport.contracts import RackFace


async def confirm_rack_faces(db_engine: object, rack_faces: dict[str, RackFace]) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    await confirm_rack_faces_with_sessions(sessions, rack_faces)


async def confirm_rack_faces_with_sessions(
    sessions: async_sessionmaker[AsyncSession],
    rack_faces: dict[str, RackFace],
) -> None:
    async with sessions.begin() as db:
        for rack_id, rack_face in rack_faces.items():
            projection = await db.scalar(
                select(TransportPositionProjection).where(
                    TransportPositionProjection.object_type == "RACK",
                    TransportPositionProjection.object_id == rack_id,
                )
            )
            if projection is None:
                db.add(
                    TransportPositionProjection(
                        object_type="RACK",
                        object_id=rack_id,
                        position_json={"kind": "RACK_POSITION", "location_code": f"STORAGE-{rack_id}"},
                        arrival_face=str(rack_face),
                        source_operation_id="test-confirmed-rack-face",
                        updated_at=timezone.now_for_db(),
                    )
                )
                continue
            projection.position_unknown = False
            projection.arrival_face = str(rack_face)
            projection.source_operation_id = "test-confirmed-rack-face"
            projection.updated_at = timezone.now_for_db()


__all__ = ["confirm_rack_faces", "confirm_rack_faces_with_sessions"]
