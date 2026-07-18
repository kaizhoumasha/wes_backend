"""插件单次 Inbox attempt 的三阶段 QUERY/重校验/原子写回协调器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from src.app.runtime.extension_identity import canonical_json
from src.app.runtime.workline_plugins.contracts import MAX_PLUGIN_DECISION_INTENTS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    """Stage 1 短事务固定的 lease 与乐观版本。"""

    processor_token: str
    session_version: int
    plugin_state_version: int
    session_status: str | None = None
    material_unit_id: int | None = None
    material_unit_version: int | None = None
    definition_identity: str | None = None
    binding_id: int | None = None
    binding_version: int | None = None
    plugin_config_hash: str | None = None
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
    recorded_attempt_anchor: Any | None = None
    recorded_decision: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PluginWriteSetLimits:
    """单 attempt 决策写集合的 canonical UTF-8 资源上限。"""

    max_next_state_bytes: int = 64 * 1024
    max_intent_bytes: int = 64 * 1024
    max_intents_total_bytes: int = 256 * 1024
    max_write_set_bytes: int = 384 * 1024
    max_intents: int = MAX_PLUGIN_DECISION_INTENTS

    def __post_init__(self) -> None:
        if (
            min(
                self.max_next_state_bytes,
                self.max_intent_bytes,
                self.max_intents_total_bytes,
                self.max_write_set_bytes,
                self.max_intents,
            )
            <= 0
        ):
            raise ValueError("plugin write-set limits must be positive")
        if self.max_intents > MAX_PLUGIN_DECISION_INTENTS:
            raise ValueError("plugin intent count limit exceeds contract maximum")


def bound_attempt_write_set(write_set: AttemptWriteSet, *, limits: Any) -> AttemptWriteSet:
    """在 hash/timeline/ledger 之前执行统一 canonical UTF-8 边界校验。"""

    try:
        state_value = _json_value(write_set.next_state)
        intent_values = tuple(_json_value(intent) for intent in write_set.intents)
        intent_sizes = tuple(_canonical_bytes(value) for value in intent_values)
        whole_value = {
            "evidence": [_json_value(item) for item in write_set.evidence],
            "next_state": state_value,
            "intents": list(intent_values),
            "outcome_code": write_set.outcome_code,
            "hold_reason": write_set.hold_reason,
        }
        exceeded = (
            len(intent_values) > limits.max_intents
            or _canonical_bytes(state_value) > limits.max_next_state_bytes
            or any(size > limits.max_intent_bytes for size in intent_sizes)
            or sum(intent_sizes) > limits.max_intents_total_bytes
            or _canonical_bytes(whole_value) > limits.max_write_set_bytes
        )
    except (TypeError, ValueError):
        exceeded = True
    if not exceeded:
        return write_set
    return AttemptWriteSet(
        evidence=(),
        next_state={},
        intents=(),
        outcome_code="HOLD",
        hold_reason="PLUGIN_WRITE_SET_LIMIT_EXCEEDED",
    )


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _canonical_bytes(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8"))


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
    # Stage 1 已构造并冻结的 typed dispatcher 请求；Stage 2 不得回读 DB。
    dispatch_request: Any | None = None


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
    "PluginWriteSetLimits",
    "UnavailablePluginAttemptRunner",
    "WriteDisposition",
    "bound_attempt_write_set",
]
