"""WorkLine plane scene/snapshot read service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.workline.models import PlaneNode, PlaneSceneView, PlaneSnapshot, WorkLine
from src.app.workline.services.workline_service import workline_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WorkLinePlaneService:
    """WorkLine 平面态势读模型服务。"""

    async def get_scene(self, db: AsyncSession, cache: Any, workline_id: int) -> PlaneSceneView:
        """读取 WorkLine 静态平面 scene。"""

        workline = await self._load_workline(db, cache, workline_id)
        return self.build_scene(workline)

    async def get_snapshot(self, db: AsyncSession, cache: Any, workline_id: int) -> PlaneSnapshot:
        """读取 WorkLine 动态平面 snapshot。"""

        workline = await self._load_workline(db, cache, workline_id)
        return self.build_snapshot(workline)

    async def _load_workline(self, db: AsyncSession, cache: Any, workline_id: int) -> WorkLine:
        workline = await workline_service.get_by_id(db, cache, workline_id, max_depth=0)
        if workline is None:
            raise ValueError(f"作业线不存在: {workline_id}")
        return workline

    def build_scene(self, workline: WorkLine) -> PlaneSceneView:
        """从 WorkLine 配置派生首版 plane scene。"""

        nodes = [
            PlaneNode(
                code=workline.line_code,
                label=workline.line_name,
                kind="WORKLINE",
            )
        ]
        nodes.extend(self._build_queue_nodes(workline))
        return PlaneSceneView(
            schema_version="plane.scene.v1",
            workline_code=workline.line_code,
            nodes=nodes,
            edges=[],
        )

    def build_snapshot(self, workline: WorkLine) -> PlaneSnapshot:
        """从 active projection 派生首版 plane snapshot。

        Phase 3 首个 PR 先提供稳定响应壳，后续 projection assembler 接入后
        在本方法内部补 objects/extremes，不改变 API 合同。
        """

        return PlaneSnapshot(
            schema_version="plane.snapshot.v1",
            workline_code=workline.line_code,
            scene_schema_version="plane.scene.v1",
            objects=[],
            extremes=[],
        )

    @staticmethod
    def _build_queue_nodes(workline: WorkLine) -> list[PlaneNode]:
        config = workline.config if isinstance(workline.config, dict) else {}
        raw_queues = config.get("pipeline_queues")
        if not isinstance(raw_queues, list):
            return []

        nodes: list[PlaneNode] = []
        for item in raw_queues:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            if not isinstance(code, str) or not code.strip():
                continue
            role = item.get("role")
            nodes.append(
                PlaneNode(
                    code=code.strip(),
                    label=str(item.get("label") or code).strip(),
                    kind=str(role or "QUEUE").strip(),
                )
            )
        return nodes


workline_plane_service = WorkLinePlaneService()


__all__ = ["WorkLinePlaneService", "workline_plane_service"]
