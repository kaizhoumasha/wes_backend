"""发布静默门禁的唯一状态判定所有者。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.runtime.orchestration.repositories.release_operational_readiness_repository import (
    ReleaseOperationalReadinessRepository,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

READINESS_QUERY_TIMEOUT_SECONDS = 10
COUNT_KEYS = (
    "device_command_wait_drain",
    "device_command_block",
    "device_command_unknown",
    "device_command_invalid",
    "transport_task_wait_drain",
    "transport_task_block",
    "transport_task_unknown",
    "transport_task_invalid",
    "inbound_evidence_wait_drain",
    "inbound_evidence_block",
    "inbound_evidence_unknown",
    "inbound_evidence_invalid",
    "wms_confirmation_wait_drain",
    "wms_confirmation_block",
    "wms_confirmation_unknown",
    "wms_confirmation_invalid",
)
FAIL_CLOSED_KEYS = tuple(key for key in COUNT_KEYS if key.endswith(("_unknown", "_invalid")))
WAIT_DRAIN_KEYS = tuple(key for key in COUNT_KEYS if key.endswith("_wait_drain"))
BLOCK_KEYS = tuple(key for key in COUNT_KEYS if key.endswith("_block"))


class ReleaseOperationalReadinessQueryError(RuntimeError):
    """门禁查询失败或返回不可接受的分类计数。"""


@dataclass(frozen=True)
class ReleaseOperationalReadinessResult:
    state: str
    counts: dict[str, int]
    wait_drain_total: int
    block_total: int
    generated_at: str


class ReleaseOperationalReadinessService:
    def __init__(self, repository: object | None = None) -> None:
        self._repository = repository or ReleaseOperationalReadinessRepository()

    async def check(self, db: AsyncSession) -> ReleaseOperationalReadinessResult:
        try:
            async with asyncio.timeout(READINESS_QUERY_TIMEOUT_SECONDS):
                snapshot = await self._repository.load_counts(db)  # type: ignore[attr-defined]
            values = vars(snapshot)
            if set(values) != set(COUNT_KEYS):
                raise ValueError("unexpected readiness count shape")
            counts = {key: values[key] for key in COUNT_KEYS}
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
                raise ValueError("invalid readiness count")
            if any(counts[key] for key in FAIL_CLOSED_KEYS):
                raise ValueError("unknown or impossible readiness row")
        except Exception as exc:
            raise ReleaseOperationalReadinessQueryError("release operational readiness query failed") from exc

        wait_drain_total = sum(counts[key] for key in WAIT_DRAIN_KEYS)
        block_total = sum(counts[key] for key in BLOCK_KEYS)
        state = "BLOCK" if block_total else "WAIT_DRAIN" if wait_drain_total else "READY"
        return ReleaseOperationalReadinessResult(
            state=state,
            counts=counts,
            wait_drain_total=wait_drain_total,
            block_total=block_total,
            generated_at=timezone.now_utc().isoformat(),
        )


__all__ = [
    "ReleaseOperationalReadinessQueryError",
    "ReleaseOperationalReadinessResult",
    "ReleaseOperationalReadinessService",
]
