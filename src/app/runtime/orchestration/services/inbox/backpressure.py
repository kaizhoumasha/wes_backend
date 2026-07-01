"""RuntimeInbox backpressure policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeInboxBackpressureDecision:
    """RuntimeInbox 积压判定结果。"""

    mode: str
    reason: str
    accept_new_messages: bool
    dispatch_immediate_processing: bool


@dataclass(frozen=True, slots=True)
class RuntimeInboxBackpressurePolicy:
    """RuntimeInbox backpressure 阈值策略。"""

    max_pending: int
    dead_letter_threshold: int

    def evaluate(self, *, pending_count: int, dead_letter_count: int) -> RuntimeInboxBackpressureDecision:
        if dead_letter_count >= self.dead_letter_threshold:
            return RuntimeInboxBackpressureDecision(
                mode="OPERATOR_ATTENTION",
                reason="DEAD_LETTER_BACKLOG",
                accept_new_messages=True,
                dispatch_immediate_processing=False,
            )
        if pending_count >= self.max_pending:
            return RuntimeInboxBackpressureDecision(
                mode="DEGRADED",
                reason="PENDING_BACKLOG",
                accept_new_messages=True,
                dispatch_immediate_processing=False,
            )
        return RuntimeInboxBackpressureDecision(
            mode="NORMAL",
            reason="OK",
            accept_new_messages=True,
            dispatch_immediate_processing=True,
        )


__all__ = ["RuntimeInboxBackpressureDecision", "RuntimeInboxBackpressurePolicy"]
