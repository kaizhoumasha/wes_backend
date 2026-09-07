"""Transport 核心测试使用的可靠货架工作面事实。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.execution.models import PositionProjection
from src.app.workline.models.workline import LineType, WorkLine
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.transport.contracts import TransportExecutionAuthority


async def confirm_rack_faces(db_engine: object, rack_faces: dict[str, str]) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    await confirm_rack_faces_with_sessions(sessions, rack_faces)


async def confirm_rack_faces_with_sessions(
    sessions: async_sessionmaker[AsyncSession],
    rack_faces: dict[str, str],
) -> None:
    async with sessions.begin() as db:
        workline_id = await ensure_projection_authority(db)
        for rack_id, rack_face in rack_faces.items():
            projection = await db.scalar(
                select(PositionProjection).where(
                    PositionProjection.object_type == "RACK",
                    PositionProjection.object_id == rack_id,
                )
            )
            if projection is None:
                db.add(
                    PositionProjection(
                        object_type="RACK",
                        object_id=rack_id,
                        workline_id=workline_id,
                        position_json={"kind": "RACK_POSITION", "location_code": f"STORAGE-{rack_id}"},
                        arrival_face=rack_face,
                        source_operation_id="test-confirmed-rack-face",
                        source_transport_task_id="test-confirmed-rack-face",
                        updated_at=timezone.now_for_db(),
                    )
                )
                continue
            projection.position_unknown = False
            projection.arrival_face = rack_face
            projection.source_operation_id = "test-confirmed-rack-face"
            projection.updated_at = timezone.now_for_db()


async def ensure_projection_authority_with_sessions(
    sessions: async_sessionmaker[AsyncSession],
) -> TransportExecutionAuthority:
    from src.app.transport.contracts import TransportExecutionAuthority

    async with sessions.begin() as db:
        workline_id = await ensure_projection_authority(db)
    return TransportExecutionAuthority(
        workline_id=workline_id,
    )


async def ensure_projection_authority(db: AsyncSession) -> int:
    line = await db.scalar(select(WorkLine).where(WorkLine.line_code == "TRANSPORT-PROJECTION-TEST"))
    if line is None:
        line = WorkLine(
            line_code="TRANSPORT-PROJECTION-TEST",
            line_name="Transport projection test authority",
            line_type=LineType.AUTO,
            is_active=True,
            plugin_key="rough_sorter",
            plugin_version="1.0.0",
            flow_mode="TRANSPORT_TEST",
        )
        db.add(line)
        await db.flush()
    if line.id is None:
        raise RuntimeError("test projection authority was not persisted")
    return line.id


__all__ = [
    "confirm_rack_faces",
    "confirm_rack_faces_with_sessions",
    "ensure_projection_authority",
    "ensure_projection_authority_with_sessions",
]
