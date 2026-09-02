"""Runtime orchestration 的统一 trace 传播上下文。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from src.utils.value_normalization import (
    as_dict,
    canonical_event_type,
    enum_value,
    optional_int,
    optional_int_attr,
    optional_str,
    optional_str_attr,
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """组合 ingress、session、command、outbox 与 timeline 的 trace 字段。"""

    request_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    causation_id: str | None = None
    workline_id: int | None = None
    session_id: int | None = None
    inbox_id: int | None = None
    device_id: int | None = None
    device_code: str | None = None
    command_id: int | None = None
    command_code: str | None = None
    outbox_id: int | None = None
    dispatch_key: str | None = None
    canonical_event_type: str | None = None
    transition: str | None = None

    @classmethod
    def from_request(
        cls,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        device_id: int | None = None,
        device_code: str | None = None,
        canonical_event_type: str | None = None,
        transition: str | None = None,
    ) -> TraceContext:
        """从入口请求创建最小 trace 上下文。"""

        return cls(
            request_id=optional_str(request_id),
            trace_id=optional_str(trace_id),
            event_id=optional_str(event_id),
            causation_id=optional_str(causation_id),
            device_id=optional_int(device_id),
            device_code=optional_str(device_code),
            canonical_event_type=optional_str(canonical_event_type),
            transition=optional_str(transition),
        )

    @classmethod
    def from_runtime(
        cls,
        *,
        session: Any | None = None,
        workline: Any | None = None,
        inbox: Any | None = None,
        command: Any | None = None,
        outbox: Any | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        canonical_event_type: str | None = None,
        transition: str | None = None,
    ) -> TraceContext:
        """从运行时实体组合 trace 上下文。"""

        trace = cls.from_request(
            request_id=request_id,
            trace_id=trace_id,
            canonical_event_type=canonical_event_type,
            transition=transition,
        )
        if workline is not None:
            trace = trace.with_workline(workline)
        if session is not None:
            trace = trace.with_session(session)
        if inbox is not None:
            trace = trace.with_inbox(inbox)
        if command is not None:
            trace = trace.with_command(command)
        if outbox is not None:
            trace = trace.with_outbox(outbox)
        return trace

    def _bind(self, **updates: Any) -> TraceContext:
        return replace(self, **{key: value for key, value in updates.items() if value is not None})

    def with_request_id(self, request_id: str | None) -> TraceContext:
        return self._bind(request_id=optional_str(request_id) or self.request_id)

    def with_trace_id(self, trace_id: str | None) -> TraceContext:
        return self._bind(trace_id=optional_str(trace_id) or self.trace_id)

    def with_event_identity(self, *, event_id: str | None = None, causation_id: str | None = None) -> TraceContext:
        return self._bind(
            event_id=optional_str(event_id) or self.event_id,
            causation_id=optional_str(causation_id) or self.causation_id,
        )

    def with_workline(self, workline: Any) -> TraceContext:
        return self._bind(workline_id=optional_int_attr(workline, "id") or self.workline_id)

    def with_session(self, session: Any) -> TraceContext:
        return self._bind(
            request_id=optional_str_attr(session, "last_request_id") or self.request_id,
            trace_id=optional_str_attr(session, "trace_id") or self.trace_id,
            workline_id=optional_int_attr(session, "workline_id") or self.workline_id,
            session_id=optional_int_attr(session, "id") or self.session_id,
        )

    def with_inbox(self, inbox: Any) -> TraceContext:
        payload = as_dict(getattr(inbox, "payload_json", None))
        return self._bind(
            request_id=optional_str_attr(inbox, "source_message_id") or self.request_id,
            trace_id=optional_str_attr(inbox, "trace_id") or self.trace_id,
            event_id=optional_str_attr(inbox, "event_id") or optional_str(payload.get("event_id")) or self.event_id,
            causation_id=optional_str_attr(inbox, "causation_id")
            or optional_str(payload.get("causation_id"))
            or self.causation_id,
            workline_id=optional_int_attr(inbox, "workline_id") or self.workline_id,
            session_id=optional_int_attr(inbox, "workline_session_ref")
            or optional_int_attr(inbox, "session_id")
            or self.session_id,
            inbox_id=optional_int_attr(inbox, "id") or self.inbox_id,
            device_id=optional_int_attr(inbox, "device_id") or self.device_id,
            command_id=optional_int_attr(inbox, "command_id") or self.command_id,
            device_code=optional_str(payload.get("device_code")) or self.device_code,
            command_code=optional_str(payload.get("command_code")) or self.command_code,
            canonical_event_type=canonical_event_type(payload) or self.canonical_event_type,
        )

    def with_device(self, device: Any) -> TraceContext:
        return self._bind(
            workline_id=optional_int_attr(device, "work_line_id") or self.workline_id,
            device_id=optional_int_attr(device, "id") or self.device_id,
            device_code=optional_str_attr(device, "device_code") or self.device_code,
        )

    def with_device_code(self, device_code: str | None) -> TraceContext:
        return self._bind(device_code=optional_str(device_code) or self.device_code)

    def with_command(self, command: Any) -> TraceContext:
        return self._bind(
            command_id=optional_int_attr(command, "id") or self.command_id,
            command_code=optional_str_attr(command, "command_code") or self.command_code,
            trace_id=optional_str_attr(command, "trace_id") or self.trace_id,
            workline_id=optional_int_attr(command, "workline_id") or self.workline_id,
            device_id=optional_int_attr(command, "device_id") or self.device_id,
        )

    def with_command_code(self, command_code: str | None) -> TraceContext:
        return self._bind(command_code=optional_str(command_code) or self.command_code)

    def with_outbox(self, outbox: Any) -> TraceContext:
        return self._bind(
            outbox_id=optional_int_attr(outbox, "id") or self.outbox_id,
            dispatch_key=optional_str_attr(outbox, "dispatch_key") or self.dispatch_key,
            workline_id=optional_int_attr(outbox, "workline_id") or self.workline_id,
            session_id=optional_int_attr(outbox, "session_id") or self.session_id,
        )

    def with_dispatch_key(self, dispatch_key: str | None) -> TraceContext:
        return self._bind(dispatch_key=optional_str(dispatch_key) or self.dispatch_key)

    def with_canonical_event_type(self, event_type: str | None) -> TraceContext:
        return self._bind(canonical_event_type=optional_str(event_type) or self.canonical_event_type)

    def with_transition(self, transition: str | None) -> TraceContext:
        return self._bind(transition=optional_str(transition) or self.transition)

    def project_timeline_payload(self, **extra: Any) -> dict[str, Any]:
        """投影成 timeline payload 的统一基础字段。"""

        payload = {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "event_id": self.event_id,
            "causation_id": self.causation_id,
            "canonical_event_type": self.canonical_event_type,
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        return payload

    def project_outbox_trace(self, *, outbox: Any | None = None, **extra: Any) -> dict[str, Any]:
        """投影成 outbox dispatch 记录的稳定 trace 字段。"""

        dispatch_type = enum_value(extra.pop("dispatch_type", None))
        target_code = extra.pop("target_code", None)
        if outbox is not None:
            dispatch_type = dispatch_type or enum_value(getattr(outbox, "dispatch_type", None))
            target_code = target_code or optional_str_attr(outbox, "target_code")
        payload: dict[str, Any] = {
            "outbox_id": self.outbox_id or optional_int_attr(outbox, "id"),
            "dispatch_key": self.dispatch_key or optional_str_attr(outbox, "dispatch_key"),
            "dispatch_type": dispatch_type,
            "target_code": target_code,
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        return payload

    def as_dict(self) -> dict[str, Any]:
        """返回适合日志与调试输出的紧凑字典。"""

        return {key: value for key, value in asdict(self).items() if value is not None}


__all__ = ["TraceContext"]
