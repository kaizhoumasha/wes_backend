"""插件单次 Inbox attempt 的三阶段 QUERY/重校验/原子写回协调器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    """Stage 1 短事务固定的 lease 与乐观版本。"""

    processor_token: str
    session_version: int
    plugin_state_version: int


@dataclass(frozen=True, slots=True)
class AttemptWriteSet:
    """Stage 2 纯 QUERY 产生、仅能在 Stage 3 原子落库的写集合。"""

    evidence: tuple[Any, ...]
    next_state: Any
    intents: tuple[Any, ...]


class WriteDisposition(str, Enum):
    COMMITTED = "COMMITTED"
    SAFE_RETRY = "SAFE_RETRY"


class AttemptCoordinator:
    """不接收 DB；确保外部 QUERY 与写事务在 API 上隔离。"""

    def __init__(self, snapshot: AttemptSnapshot) -> None:
        self.snapshot = snapshot

    async def execute(
        self,
        *,
        query_phase: Callable[[], Awaitable[tuple[Any, ...]]],
        current_snapshot: Callable[[], Awaitable[AttemptSnapshot]],
        build_write_set: Callable[[tuple[Any, ...]], AttemptWriteSet],
        writeback: Callable[[AttemptWriteSet], Awaitable[None]],
    ) -> WriteDisposition:
        """QUERY 无 DB 参数；短事务重校验通过后才构造并提交写集合。"""

        evidence = await query_phase()
        if await current_snapshot() != self.snapshot:
            return WriteDisposition.SAFE_RETRY
        await writeback(build_write_set(evidence))
        return WriteDisposition.COMMITTED


__all__ = ["AttemptCoordinator", "AttemptSnapshot", "AttemptWriteSet", "WriteDisposition"]
