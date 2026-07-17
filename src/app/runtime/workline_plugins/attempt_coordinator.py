"""插件单次 Inbox attempt 的三阶段 QUERY/重校验/原子写回协调器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    """Stage 1 短事务固定的 lease 与乐观版本。"""

    processor_token: str
    session_version: int
    plugin_state_version: int
    definition_identity: str | None = None
    binding_id: int | None = None
    binding_version: int | None = None
    index_digest: str | None = None

    @property
    def binding_identity(self) -> str | None:
        """返回 recorded replay 使用的 immutable binding identity。"""

        if self.binding_id is None or self.binding_version is None:
            return None
        return f"binding:{self.binding_id}:{self.binding_version}"


@dataclass(frozen=True, slots=True)
class AttemptWriteSet:
    """Stage 2 纯 QUERY 产生、仅能在 Stage 3 原子落库的写集合。"""

    evidence: tuple[Any, ...]
    next_state: Any
    intents: tuple[Any, ...]
    outcome_code: str = "UNSPECIFIED"
    hold_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PluginAttemptContext:
    """Stage 2 唯一输入；仅含 immutable primitives，不携带持久化对象。"""

    attempt_id: str
    inbox_id: int
    session_id: int
    workline_id: int
    event_type: str
    payload: dict[str, Any]
    plugin_state: dict[str, Any]
    snapshot: AttemptSnapshot
    runtime: Any


class PluginAttemptRunner(Protocol):
    """平台插件 Stage 2 runner 合同。"""

    async def run(self, context: PluginAttemptContext) -> AttemptWriteSet: ...


class UnavailablePluginAttemptRunner:
    """平台 binding 已存在但 runner 未接线时 fail closed，禁止回落 legacy。"""

    async def run(self, context: PluginAttemptContext) -> AttemptWriteSet:
        return AttemptWriteSet(
            evidence=(),
            next_state=context.plugin_state,
            intents=(),
            outcome_code="HOLD",
            hold_reason="PLUGIN_ATTEMPT_RUNNER_UNAVAILABLE",
        )


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


__all__ = [
    "AttemptCoordinator",
    "AttemptSnapshot",
    "AttemptWriteSet",
    "PluginAttemptContext",
    "PluginAttemptRunner",
    "UnavailablePluginAttemptRunner",
    "WriteDisposition",
]
