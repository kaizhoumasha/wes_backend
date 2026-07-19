"""插件单次 Inbox attempt 的三阶段 QUERY/重校验/原子写回协调器。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

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
    device_fact_versions: tuple[tuple[str, int, int], ...] = ()
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
    preserve_plugin_state: bool = False


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


def bound_attempt_write_set(
    write_set: AttemptWriteSet,
    *,
    limits: Any,
    fallback_state: Any,
    allow_state_preservation: bool = False,
) -> AttemptWriteSet:
    """在 hash/timeline/ledger 之前执行统一 canonical UTF-8 边界校验。"""

    try:
        state_value = _json_value(write_set.next_state)
        if not isinstance(state_value, dict):
            raise TypeError("plugin next state must be an object")
        intent_values = tuple(_json_value(intent) for intent in write_set.intents)
        intent_sizes = tuple(_canonical_bytes(value) for value in intent_values)
        recorded_decision_value = None
        recorded_state_value = None
        recorded_intent_values: tuple[Any, ...] = ()
        if write_set.recorded_decision is not None:
            recorded_decision_value = _json_value(write_set.recorded_decision)
            if not isinstance(recorded_decision_value, dict):
                raise TypeError("recorded decision must be an object")
            recorded_state_value = recorded_decision_value.get("next_state")
            if not isinstance(recorded_state_value, dict):
                raise TypeError("recorded decision next state must be an object")
            raw_recorded_intents = recorded_decision_value.get("intents")
            if not isinstance(raw_recorded_intents, list):
                raise TypeError("recorded decision intents must be an array")
            recorded_intent_values = tuple(raw_recorded_intents)
        recorded_anchor_value = (
            _json_value(write_set.recorded_attempt_anchor) if write_set.recorded_attempt_anchor is not None else None
        )
        recorded_intent_sizes = tuple(_canonical_bytes(value) for value in recorded_intent_values)
        preserve_plugin_state = allow_state_preservation and write_set.preserve_plugin_state
        whole_value = {
            "evidence": [_json_value(item) for item in write_set.evidence],
            "next_state": state_value,
            "intents": list(intent_values),
            "outcome_code": write_set.outcome_code,
            "hold_reason": write_set.hold_reason,
            "preserve_plugin_state": preserve_plugin_state,
        }
        if recorded_decision_value is not None:
            whole_value["recorded_decision"] = recorded_decision_value
        if recorded_anchor_value is not None:
            whole_value["recorded_attempt_anchor"] = recorded_anchor_value
        exceeded = (
            len(intent_values) > limits.max_intents
            or _canonical_bytes(state_value) > limits.max_next_state_bytes
            or any(size > limits.max_intent_bytes for size in intent_sizes)
            or sum(intent_sizes) > limits.max_intents_total_bytes
            or len(recorded_intent_values) > limits.max_intents
            or (
                recorded_state_value is not None
                and _canonical_bytes(recorded_state_value) > limits.max_next_state_bytes
            )
            or any(size > limits.max_intent_bytes for size in recorded_intent_sizes)
            or sum(recorded_intent_sizes) > limits.max_intents_total_bytes
            or _canonical_bytes(whole_value) > limits.max_write_set_bytes
        )
    except (RecursionError, TypeError, ValueError):
        exceeded = True
    if not exceeded:
        # 返回与 runner 所持引用隔离的校验快照，避免 Stage 3 后续 await
        # 期间嵌套可变对象被改写并绕过严格 JSON/资源边界。
        return AttemptWriteSet(
            evidence=deepcopy(write_set.evidence),
            next_state=deepcopy(state_value),
            intents=deepcopy(write_set.intents),
            outcome_code=write_set.outcome_code,
            hold_reason=write_set.hold_reason,
            recorded_attempt_anchor=deepcopy(recorded_anchor_value),
            recorded_decision=deepcopy(recorded_decision_value),
            preserve_plugin_state=preserve_plugin_state,
        )
    return AttemptWriteSet(
        evidence=(),
        next_state={},
        intents=(),
        outcome_code="HOLD",
        hold_reason="PLUGIN_WRITE_SET_LIMIT_EXCEEDED",
        preserve_plugin_state=True,
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
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return len(encoded.encode("utf-8"))


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
