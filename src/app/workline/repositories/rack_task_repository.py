"""兼容导出：WorklineRackTaskRepository 已迁移为 RackTaskRepository。"""

from __future__ import annotations

from src.app.rack.repositories import RackTaskRepository as WorklineRackTaskRepository
from src.app.rack.repositories import rack_task_repository as workline_rack_task_repository

__all__ = ["WorklineRackTaskRepository", "workline_rack_task_repository"]
