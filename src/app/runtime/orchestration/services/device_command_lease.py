"""DeviceCommand lease policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class DeviceCommandLease:
    """单条 DeviceCommand 的 lease 快照。"""

    command_code: str
    device_code: str
    leased_at: int
    lease_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class DeviceCommandLeaseDecision:
    """DeviceCommand lease 判定结果。"""

    expired: bool
    replay_allowed: bool
    cancel_allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class DeviceCommandLeasePolicy:
    """DeviceCommand 过期 lease 重放/取消策略。"""

    default_lease_seconds: int

    def evaluate(self, lease: DeviceCommandLease, *, now: int) -> DeviceCommandLeaseDecision:
        lease_seconds = lease.lease_seconds or self.default_lease_seconds
        if now >= lease.leased_at + lease_seconds:
            return DeviceCommandLeaseDecision(
                expired=True,
                replay_allowed=True,
                cancel_allowed=True,
                reason="LEASE_EXPIRED",
            )
        return DeviceCommandLeaseDecision(
            expired=False,
            replay_allowed=False,
            cancel_allowed=False,
            reason="LEASE_ACTIVE",
        )

    def evaluate_command(self, command: Any, *, now: datetime) -> DeviceCommandLeaseDecision:
        """直接基于 DeviceCommand 快照判定 lease 是否过期。

        sent_at 是数据库 naive UTC 时间，不能调用 `.timestamp()`；这里使用
        datetime 差值避免时区误用。
        """

        sent_at = getattr(command, "sent_at", None)
        if not isinstance(sent_at, datetime):
            return DeviceCommandLeaseDecision(
                expired=False,
                replay_allowed=False,
                cancel_allowed=False,
                reason="LEASE_NOT_STARTED",
            )

        timeout_ms = getattr(command, "timeout_ms", None)
        if isinstance(timeout_ms, int) and timeout_ms > 0:
            lease_delta = timedelta(milliseconds=timeout_ms)
        else:
            lease_delta = timedelta(seconds=self.default_lease_seconds)

        if now >= sent_at + lease_delta:
            return DeviceCommandLeaseDecision(
                expired=True,
                replay_allowed=True,
                cancel_allowed=True,
                reason="LEASE_EXPIRED",
            )
        return DeviceCommandLeaseDecision(
            expired=False,
            replay_allowed=False,
            cancel_allowed=False,
            reason="LEASE_ACTIVE",
        )


__all__ = ["DeviceCommandLease", "DeviceCommandLeaseDecision", "DeviceCommandLeasePolicy"]
