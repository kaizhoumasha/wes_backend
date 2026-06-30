"""WorklineSession 生命周期领域规则。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar

from src.app.runtime.orchestration.models.session import SessionStatus


class InvalidSessionTransition(ValueError):
    """会话状态流转不合法。"""


class WorklineSessionLifecycleService:
    """集中维护 WorklineSession 的纯状态流转规则。"""

    _TERMINAL_STATUSES: ClassVar[set[str]] = {
        SessionStatus.COMPLETED.value,
        SessionStatus.FAILED.value,
        SessionStatus.CANCELLED.value,
    }

    def _status_value(self, status: object) -> str:
        val = getattr(status, "value", status)
        return val if isinstance(val, str) else str(val)

    def _ensure_not_terminal(self, session: Any, *, target: SessionStatus) -> None:
        current = self._status_value(getattr(session, "status", ""))
        if current in self._TERMINAL_STATUSES:
            raise InvalidSessionTransition(f"Cannot transition session from {current} to {target.value}")

    def clear_wait(self, session: Any) -> None:
        """清理等待态字段。"""

        session.current_wait_type = None
        session.waiting_since = None
        session.deadline_at = None
        session.current_wait_timeout_seconds = None
        session.awaiting_device_command_code = None

    def clear_failure(self, session: Any) -> None:
        """清理失败字段。"""

        session.failure_domain = None
        session.failure_code = None
        session.failure_message = None

    def complete(self, session: Any, *, occurred_at: datetime) -> None:
        """标记会话完成。"""

        self._ensure_not_terminal(session, target=SessionStatus.COMPLETED)
        session.status = SessionStatus.COMPLETED
        self.clear_wait(session)
        session.ended_at = occurred_at

    def fail(
        self,
        session: Any,
        *,
        occurred_at: datetime,
        failure_domain: str | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        """标记会话失败。"""

        self._ensure_not_terminal(session, target=SessionStatus.FAILED)
        session.status = SessionStatus.FAILED
        self.clear_wait(session)
        session.ended_at = occurred_at
        session.failure_domain = failure_domain
        session.failure_code = failure_code
        session.failure_message = failure_message

    def cancel(self, session: Any, *, occurred_at: datetime) -> None:
        """标记会话取消。"""

        self._ensure_not_terminal(session, target=SessionStatus.CANCELLED)
        session.status = SessionStatus.CANCELLED
        self.clear_wait(session)
        session.ended_at = occurred_at

    def start_wait(
        self,
        session: Any,
        *,
        wait_type: str,
        occurred_at: datetime,
        awaiting_device_command_code: str | None = None,
        deadline_seconds: int | None = None,
    ) -> None:
        """进入等待态。"""

        self._ensure_not_terminal(session, target=SessionStatus.WAITING_EXTERNAL)
        session.status = self.wait_status(wait_type)
        session.current_wait_type = wait_type
        session.waiting_since = occurred_at
        session.awaiting_device_command_code = awaiting_device_command_code
        session.current_wait_timeout_seconds = deadline_seconds
        session.deadline_at = (
            None if wait_type == "COMMAND_RESULT" else self._deadline_at(occurred_at, deadline_seconds)
        )
        session.ended_at = None

    def manual_hold(self, session: Any, *, occurred_at: datetime | None = None) -> None:
        """进入人工暂停。"""

        _ = occurred_at
        self._ensure_not_terminal(session, target=SessionStatus.MANUAL_HOLD)
        session.status = SessionStatus.MANUAL_HOLD
        self.clear_wait(session)
        session.ended_at = None

    def resume(self, session: Any) -> None:
        """从人工暂停恢复到运行或等待态。"""

        self._ensure_not_terminal(session, target=SessionStatus.RUNNING)
        if getattr(session, "current_wait_type", None):
            session.status = self.wait_status(session.current_wait_type)
        else:
            session.status = SessionStatus.RUNNING
        session.ended_at = None

    def running(self, session: Any) -> None:
        """进入运行态。"""

        self._ensure_not_terminal(session, target=SessionStatus.RUNNING)
        session.status = SessionStatus.RUNNING
        session.ended_at = None

    def resolve(self, session: Any, *, resolution: SessionStatus, occurred_at: datetime) -> None:
        """人工解除后按决议终结会话。"""

        if resolution == SessionStatus.COMPLETED:
            self.complete(session, occurred_at=occurred_at)
            return
        if resolution == SessionStatus.FAILED:
            current = self._status_value(getattr(session, "status", ""))
            if current != SessionStatus.FAILED.value:
                self._ensure_not_terminal(session, target=SessionStatus.FAILED)
            session.status = SessionStatus.FAILED
            self.clear_wait(session)
            session.ended_at = occurred_at
            return
        if resolution == SessionStatus.CANCELLED:
            self.cancel(session, occurred_at=occurred_at)
            return
        raise InvalidSessionTransition(f"Unsupported runtime hold resolution: {resolution.value}")

    def replay_command_result_wait(self, session: Any, *, command_code: str, occurred_at: datetime) -> None:
        """解除 Hold 后回到等待设备结果回放态。"""

        self._ensure_not_terminal(session, target=SessionStatus.WAITING_DEVICE_RESULT)
        session.status = SessionStatus.WAITING_DEVICE_RESULT
        session.ended_at = None
        session.current_wait_type = "COMMAND_RESULT"
        session.waiting_since = occurred_at
        session.deadline_at = None
        session.current_wait_timeout_seconds = None
        session.awaiting_device_command_code = command_code
        self.clear_failure(session)

    def wait_status(self, wait_type: str) -> SessionStatus:
        """根据等待类型返回 SessionStatus。"""

        return SessionStatus.WAITING_DEVICE_RESULT if wait_type == "COMMAND_RESULT" else SessionStatus.WAITING_EXTERNAL

    def _deadline_at(self, occurred_at: datetime, deadline_seconds: int | None) -> datetime | None:
        if deadline_seconds is None:
            return None
        return occurred_at + timedelta(seconds=deadline_seconds)


workline_session_lifecycle_service = WorklineSessionLifecycleService()
