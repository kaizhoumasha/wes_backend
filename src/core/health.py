"""
轻量级系统健康状态缓存 — 供 API 层 Fast Fail 使用

由 Celery health_check 任务每 60s 更新，API 层同步读取（零 I/O）。
缓存过期时放行请求（乐观策略），避免误杀。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class SystemHealth:
    """系统健康状态（进程内单例，由 health_check 任务更新）"""

    db_ok: bool = False
    redis_ok: bool = False
    celery_ok: bool = False
    last_check: float = 0.0
    ttl: float = 30.0  # 缓存有效期（秒）

    @property
    def is_ready(self) -> bool:
        """核心服务是否就绪（DB + Celery）"""
        return self.db_ok and self.celery_ok

    @property
    def is_stale(self) -> bool:
        """缓存是否过期（过期时放行请求）"""
        return (time.time() - self.last_check) > self.ttl

    def update(
        self,
        *,
        db_ok: bool | None = None,
        redis_ok: bool | None = None,
        celery_ok: bool | None = None,
    ) -> None:
        """更新健康状态"""
        if db_ok is not None:
            self.db_ok = db_ok
        if redis_ok is not None:
            self.redis_ok = redis_ok
        if celery_ok is not None:
            self.celery_ok = celery_ok
        self.last_check = time.time()


system_health = SystemHealth()
