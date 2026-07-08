"""RuntimeInbox 目标态状态机契约（测试专用, C5）。

不 import legacy src.app.workline.models.inbox.WorklineInbox;
旧 NEW/RETRY/PROCESSING 只可作 characterization 来源, 不反向决定目标态命名。
生产路径已升级到 runtime/orchestration。

对应主计划 §9.2 RuntimeInbox 处理契约 + §7.5 C5。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

InboxStatus = Literal["RECEIVED", "PROCESSING", "PROCESSED", "FAILED", "DEAD_LETTER"]

# 合法状态转移 (主计划 §9.2 + SPEC P0-007 C5)
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "RECEIVED": {"PROCESSING"},
    "PROCESSING": {"PROCESSED", "FAILED", "RECEIVED"},  # PROCESSED 成功 / FAILED 可重试 / lease 过期回 RECEIVED
    "FAILED": {"RECEIVED", "DEAD_LETTER"},  # 到 next_retry_at 回 RECEIVED / 超限 DEAD_LETTER
    "DEAD_LETTER": set(),  # 终态, 只能人工重放生成新 inbox 记录
    "PROCESSED": set(),  # 终态
}

TERMINAL_STATES = {"PROCESSED", "DEAD_LETTER"}


@dataclass
class RuntimeInboxEntry:
    """RuntimeInbox 目标态条目。"""

    status: InboxStatus = "RECEIVED"
    attempt_count: int = 0
    next_retry_at: float | None = None
    lease_until: float | None = None
    max_retries: int = 5
    payload_hash: str = ""
    source_event_id: str = ""
    metadata: dict = field(default_factory=dict)


def transition(entry: RuntimeInboxEntry, to_status: InboxStatus, *, now: float) -> RuntimeInboxEntry:
    """执行状态转移; 非法转移抛 ValueError。"""
    legal = LEGAL_TRANSITIONS.get(entry.status, set())
    if to_status not in legal:
        raise ValueError(f"非法转移: {entry.status} -> {to_status}")
    if entry.status == "PROCESSING" and to_status == "RECEIVED":
        if entry.lease_until is None:
            raise ValueError("PROCESSING -> RECEIVED 需要 lease_until")
        if now < entry.lease_until:
            raise ValueError("PROCESSING -> RECEIVED 只能在 lease 过期后执行")

    entry.status = to_status
    if to_status == "PROCESSING":
        entry.lease_until = now + 30.0  # lease 默认 30s
    elif to_status == "FAILED":
        entry.attempt_count += 1
        entry.next_retry_at = now + 2.0 * (2 ** (entry.attempt_count - 1))  # 指数退避
        # 超过最大重试 -> DEAD_LETTER
        if entry.attempt_count > entry.max_retries:
            entry.status = "DEAD_LETTER"
            entry.next_retry_at = None
    elif to_status == "DEAD_LETTER":
        entry.next_retry_at = None
    return entry


def can_retry(entry: RuntimeInboxEntry, *, now: float) -> bool:
    """FAILED 状态到达 next_retry_at 且未超最大重试时可回 RECEIVED。"""
    if entry.status != "FAILED":
        return False
    if entry.next_retry_at is None:
        return False
    if entry.attempt_count >= entry.max_retries:
        return False
    return now >= entry.next_retry_at


def is_terminal(entry: RuntimeInboxEntry) -> bool:
    return entry.status in TERMINAL_STATES


__all__ = [
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "InboxStatus",
    "RuntimeInboxEntry",
    "can_retry",
    "is_terminal",
    "transition",
]
