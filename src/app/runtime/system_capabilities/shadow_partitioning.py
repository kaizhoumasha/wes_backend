"""QUERY shadow comparison 月分区生命周期。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

_PARTITION_NAME = re.compile(r"query_shadow_comparisons_(\d{4})_(\d{2})\Z")
_MAINTENANCE_LOCK_KEY = "query-shadow-comparison-partitions"


@dataclass(frozen=True, slots=True)
class QueryShadowPartition:
    name: str
    starts_at: datetime
    ends_at: datetime

    @property
    def create_sql(self) -> str:
        return (
            f"CREATE TABLE IF NOT EXISTS wes_runtime.{self.name} "
            "PARTITION OF wes_runtime.query_shadow_comparisons "
            f"FOR VALUES FROM ('{self.starts_at:%Y-%m-%d 00:00:00}') "
            f"TO ('{self.ends_at:%Y-%m-%d 00:00:00}')"
        )


@dataclass(frozen=True, slots=True)
class QueryShadowPartitionPlan:
    create: tuple[QueryShadowPartition, ...]


@dataclass(frozen=True, slots=True)
class QueryShadowPartitionMaintenanceResult:
    lock_acquired: bool
    created: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()


def build_query_shadow_partition_plan(now: datetime) -> QueryShadowPartitionPlan:
    """始终返回当前月和未来三个月，不生成 default partition。"""

    current = _month_start(now)
    partitions = []
    start = current
    for _ in range(4):
        end = _next_month(start)
        partitions.append(
            QueryShadowPartition(
                name=f"query_shadow_comparisons_{start:%Y_%m}",
                starts_at=start,
                ends_at=end,
            )
        )
        start = end
    return QueryShadowPartitionPlan(create=tuple(partitions))


class QueryShadowPartitionMaintainer:
    """单事务 advisory lock 下预建并在线 drop 90 天前整月分区。"""

    def __init__(self, *, retention_days: int = 90, lock_timeout_seconds: int = 5) -> None:
        if retention_days <= 0 or lock_timeout_seconds <= 0:
            raise ValueError("partition maintenance limits must be positive")
        self._retention_days = retention_days
        self._lock_timeout_seconds = lock_timeout_seconds

    async def maintain(self, db: object, *, now: datetime) -> QueryShadowPartitionMaintenanceResult:
        lock_result = await db.execute(  # type: ignore[attr-defined]
            text("SELECT pg_try_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": _MAINTENANCE_LOCK_KEY},
        )
        if not bool(lock_result.scalar_one()):
            return QueryShadowPartitionMaintenanceResult(lock_acquired=False)
        plan = build_query_shadow_partition_plan(now)
        created: list[str] = []
        for partition in plan.create:
            await db.execute(text(partition.create_sql))  # type: ignore[attr-defined]
            created.append(partition.name)
        rows = await db.execute(  # type: ignore[attr-defined]
            text(
                "SELECT child.relname FROM pg_inherits "
                "JOIN pg_class parent ON pg_inherits.inhparent = parent.oid "
                "JOIN pg_class child ON pg_inherits.inhrelid = child.oid "
                "JOIN pg_namespace namespace ON child.relnamespace = namespace.oid "
                "WHERE namespace.nspname = 'wes_runtime' AND parent.relname = 'query_shadow_comparisons'"
            )
        )
        cutoff = _as_naive_utc(now) - timedelta(days=self._retention_days)
        dropped: list[str] = []
        for row in rows.all():
            name = str(row[0])
            match = _PARTITION_NAME.fullmatch(name)
            if match is None:
                continue
            start = datetime(int(match.group(1)), int(match.group(2)), 1, tzinfo=UTC).replace(tzinfo=None)
            if _next_month(start) > cutoff:
                continue
            await db.execute(text(f"SET LOCAL lock_timeout = '{self._lock_timeout_seconds}s'"))  # type: ignore[attr-defined]
            await db.execute(text(f"DROP TABLE IF EXISTS wes_runtime.{name}"))  # type: ignore[attr-defined]
            dropped.append(name)
        return QueryShadowPartitionMaintenanceResult(
            lock_acquired=True,
            created=tuple(created),
            dropped=tuple(dropped),
        )


def _month_start(value: datetime) -> datetime:
    utc = _as_naive_utc(value)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC).replace(tzinfo=None)


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    return datetime(value.year, value.month + 1, 1, tzinfo=UTC).replace(tzinfo=None)


def _as_naive_utc(value: datetime) -> datetime:
    """分区键与保留期计算统一使用数据库语义的 naive UTC。"""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=None)
    return value.astimezone(UTC).replace(tzinfo=None)


__all__ = [
    "QueryShadowPartition",
    "QueryShadowPartitionMaintainer",
    "QueryShadowPartitionMaintenanceResult",
    "QueryShadowPartitionPlan",
    "build_query_shadow_partition_plan",
]
