"""Phase 3 DeviceCommand dispatch admission policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from datetime import datetime


class DeviceRuntimeStatus(str, Enum):
    """ECS device runtime status used by dispatch admission."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"
    MAINTENANCE = "MAINTENANCE"


class DeviceDispatchDecisionKind(str, Enum):
    """Dispatch policy decision kind."""

    ALLOW_DISPATCH = "ALLOW_DISPATCH"
    WAIT_FOR_IDLE = "WAIT_FOR_IDLE"
    RETRY_STATUS_PROBE = "RETRY_STATUS_PROBE"
    CREATE_RUNTIME_HOLD = "CREATE_RUNTIME_HOLD"
    SKIP_DEVICE = "SKIP_DEVICE"
    FREEZE_OR_CANCEL = "FREEZE_OR_CANCEL"


@dataclass(frozen=True, slots=True)
class DeviceRuntimeSnapshot:
    """Device status snapshot visible to runtime dispatch."""

    device_code: str
    status: DeviceRuntimeStatus
    observed_at: datetime
    status_valid_until: datetime
    in_flight_count: int = 0
    concurrency_limit: int = 1


@dataclass(frozen=True, slots=True)
class DeviceDispatchRequest:
    """Device command dispatch request context."""

    command_code: str
    device_role: str
    capability_code: str
    dispatch_deadline_at: datetime
    session_state: str = "RUNNING"
    priority: int = 5
    retry_attempt: int = 0


@dataclass(frozen=True, slots=True)
class DeviceDispatchDecision:
    """Result of device dispatch admission."""

    kind: DeviceDispatchDecisionKind
    reason: str
    device_code: str | None
    dispatch_allowed: bool
    runtime_hold_required: bool
    retry_after_seconds: int | None = None
    cancel_unsubmitted: bool = False
    freeze_submitted: bool = False


@dataclass(frozen=True, slots=True)
class DeviceDispatchPolicy:
    """Dispatch admission policy from the Phase 3 device contract."""

    status_snapshot_ttl_ms: int = 1000
    retry_delays_seconds: tuple[int, ...] = (1, 2, 4)
    terminal_session_states: ClassVar[frozenset[str]] = frozenset({"HOLD", "RECONCILING", "CLOSED"})

    def evaluate(  # noqa: PLR0911 - policy exits map one-to-one to dispatch decisions.
        self,
        request: DeviceDispatchRequest,
        *,
        snapshot: DeviceRuntimeSnapshot | None,
        now: datetime,
    ) -> DeviceDispatchDecision:
        """Evaluate whether a command can be dispatched now."""

        if request.session_state in self.terminal_session_states:
            return DeviceDispatchDecision(
                kind=DeviceDispatchDecisionKind.FREEZE_OR_CANCEL,
                reason=f"SESSION_{request.session_state}",
                device_code=snapshot.device_code if snapshot else None,
                dispatch_allowed=False,
                runtime_hold_required=request.session_state == "RECONCILING",
                cancel_unsubmitted=True,
                freeze_submitted=True,
            )

        if snapshot is None:
            return self._retry_or_hold(
                request,
                reason="STATUS_SNAPSHOT_MISSING",
                device_code=None,
            )

        if now > snapshot.status_valid_until:
            return self._retry_or_hold(
                request,
                reason="STATUS_SNAPSHOT_EXPIRED",
                device_code=snapshot.device_code,
            )

        if snapshot.status == DeviceRuntimeStatus.MAINTENANCE:
            return DeviceDispatchDecision(
                kind=DeviceDispatchDecisionKind.SKIP_DEVICE,
                reason="DEVICE_MAINTENANCE",
                device_code=snapshot.device_code,
                dispatch_allowed=False,
                runtime_hold_required=False,
            )

        if snapshot.status == DeviceRuntimeStatus.IDLE and snapshot.in_flight_count < snapshot.concurrency_limit:
            return DeviceDispatchDecision(
                kind=DeviceDispatchDecisionKind.ALLOW_DISPATCH,
                reason="DEVICE_IDLE",
                device_code=snapshot.device_code,
                dispatch_allowed=True,
                runtime_hold_required=False,
            )

        if snapshot.status == DeviceRuntimeStatus.RUNNING or snapshot.in_flight_count >= snapshot.concurrency_limit:
            if now < request.dispatch_deadline_at:
                return DeviceDispatchDecision(
                    kind=DeviceDispatchDecisionKind.WAIT_FOR_IDLE,
                    reason="DEVICE_BUSY",
                    device_code=snapshot.device_code,
                    dispatch_allowed=False,
                    runtime_hold_required=False,
                )
            return DeviceDispatchDecision(
                kind=DeviceDispatchDecisionKind.CREATE_RUNTIME_HOLD,
                reason="DISPATCH_DEADLINE_EXPIRED",
                device_code=snapshot.device_code,
                dispatch_allowed=False,
                runtime_hold_required=True,
            )

        if snapshot.status in {
            DeviceRuntimeStatus.ERROR,
            DeviceRuntimeStatus.OFFLINE,
            DeviceRuntimeStatus.UNKNOWN,
        }:
            return self._retry_or_hold(
                request,
                reason=f"DEVICE_{snapshot.status.value}",
                device_code=snapshot.device_code,
            )

        return DeviceDispatchDecision(
            kind=DeviceDispatchDecisionKind.CREATE_RUNTIME_HOLD,
            reason="UNSUPPORTED_DEVICE_STATUS",
            device_code=snapshot.device_code,
            dispatch_allowed=False,
            runtime_hold_required=True,
        )

    def _retry_or_hold(
        self,
        request: DeviceDispatchRequest,
        *,
        reason: str,
        device_code: str | None,
    ) -> DeviceDispatchDecision:
        if request.retry_attempt < len(self.retry_delays_seconds):
            return DeviceDispatchDecision(
                kind=DeviceDispatchDecisionKind.RETRY_STATUS_PROBE,
                reason=reason,
                device_code=device_code,
                dispatch_allowed=False,
                runtime_hold_required=False,
                retry_after_seconds=self.retry_delays_seconds[request.retry_attempt],
            )
        return DeviceDispatchDecision(
            kind=DeviceDispatchDecisionKind.CREATE_RUNTIME_HOLD,
            reason=f"{reason}_RETRY_EXHAUSTED",
            device_code=device_code,
            dispatch_allowed=False,
            runtime_hold_required=True,
        )


device_dispatch_policy = DeviceDispatchPolicy()


__all__ = [
    "DeviceDispatchDecision",
    "DeviceDispatchDecisionKind",
    "DeviceDispatchPolicy",
    "DeviceDispatchRequest",
    "DeviceRuntimeSnapshot",
    "DeviceRuntimeStatus",
    "device_dispatch_policy",
]
