"""兼容导出：Handling Outbox Repository 已迁移为 SystemOutboxRepository。"""

from __future__ import annotations

from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository

__all__ = ["SystemOutboxRepository", "system_outbox_repository"]
