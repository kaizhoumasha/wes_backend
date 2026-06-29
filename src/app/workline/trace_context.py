"""TraceContext - 统一 TRACE 传播上下文。

只保留当前阶段真正需要的最小字段，用于：
- 统一 ingress / session / command / outbox / timeline 的 trace 传播语义
- 避免各层重复拼装 request_id / trace_id / command_code
- 为 diagnostics / projector 提供轻量、可组合的上下文对象

注意：它不是运行时大对象，不承载配置、服务容器或业务状态。
"""

from __future__ import annotations

# 阶段 2 burn-down C2 镜像:src.workline_runtime.trace_context 的平级副本。
# wlr 目录在阶段 3 整体删除时,本镜像改名为正式模块并保留 consumers 旁路排除。
from dataclasses import asdict, dataclass, replace
from typing import Any

from src.app.workline.utils import non_empty_str
from src.utils.value_normalization import as_dict, enum_value, optional_int


def _resolve_int(value: Any) -> int | None:
    return optional_int(value)


def _attr_int(obj: Any, name: str) -> int | None:
    return _resolve_int(getattr(obj, name, None))


def _attr_str(obj: Any, name: str) -> str | None:
    return non_empty_str(getattr(obj, name, None))


def _resolve_payload_event_type(payload: dict[str, Any]) -> str | None:
    return non_empty_str(payload.get("canonical_event_type")) or non_empty_str(payload.get("event_type"))


@dataclass(frozen=True, slots=True)
class TraceContext:
    """轻量 Trace 传播上下文。"""

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
    plugin_key: str | None = None
    contract_version: str | None = None

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
            request_id=non_empty_str(request_id),
            trace_id=non_empty_str(trace_id),
            event_id=non_empty_str(event_id),
            causation_id=non_empty_str(causation_id),
            device_id=_resolve_int(device_id),
            device_code=non_empty_str(device_code),
            canonical_event_type=non_empty_str(canonical_event_type),
            transition=non_empty_str(transition),
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
        clean_updates = {key: value for key, value in updates.items() if value is not None}
        return replace(self, **clean_updates)

    def with_request_id(self, request_id: str | None) -> TraceContext:
        return self._bind(request_id=non_empty_str(request_id) or self.request_id)

    def with_trace_id(self, trace_id: str | None) -> TraceContext:
        resolved = non_empty_str(trace_id) or self.trace_id
        return self._bind(trace_id=resolved)

    def with_event_identity(self, *, event_id: str | None = None, causation_id: str | None = None) -> TraceContext:
        return self._bind(
            event_id=non_empty_str(event_id) or self.event_id,
            causation_id=non_empty_str(causation_id) or self.causation_id,
        )

    def with_workline(self, workline: Any) -> TraceContext:
        return self._bind(
            workline_id=_attr_int(workline, "id") or self.workline_id,
            plugin_key=_attr_str(workline, "plugin_key") or self.plugin_key,
            contract_version=_attr_str(workline, "contract_version") or self.contract_version,
        )

    def with_session(self, session: Any) -> TraceContext:
        return self._bind(
            request_id=_attr_str(session, "last_request_id") or self.request_id,
            trace_id=_attr_str(session, "trace_id") or self.trace_id,
            workline_id=_attr_int(session, "workline_id") or self.workline_id,
            session_id=_attr_int(session, "id") or self.session_id,
            plugin_key=_attr_str(session, "plugin_key") or self.plugin_key,
            contract_version=_attr_str(session, "contract_version") or self.contract_version,
        )

    def with_inbox(self, inbox: Any) -> TraceContext:
        payload = as_dict(getattr(inbox, "payload_json", None))
        return self._bind(
            request_id=_attr_str(inbox, "source_message_id") or self.request_id,
            trace_id=_attr_str(inbox, "trace_id") or self.trace_id,
            event_id=_attr_str(inbox, "event_id") or non_empty_str(payload.get("event_id")) or self.event_id,
            causation_id=_attr_str(inbox, "causation_id")
            or non_empty_str(payload.get("causation_id"))
            or self.causation_id,
            workline_id=_attr_int(inbox, "workline_id") or self.workline_id,
            session_id=_attr_int(inbox, "session_id") or self.session_id,
            inbox_id=_attr_int(inbox, "id") or self.inbox_id,
            device_id=_attr_int(inbox, "device_id") or self.device_id,
            command_id=_attr_int(inbox, "command_id") or self.command_id,
            device_code=non_empty_str(payload.get("device_code")) or self.device_code,
            command_code=non_empty_str(payload.get("command_code")) or self.command_code,
            canonical_event_type=_resolve_payload_event_type(payload) or self.canonical_event_type,
        )

    def with_device(self, device: Any) -> TraceContext:
        return self._bind(
            workline_id=_attr_int(device, "work_line_id") or self.workline_id,
            device_id=_attr_int(device, "id") or self.device_id,
            device_code=_attr_str(device, "device_code") or self.device_code,
        )

    def with_device_code(self, device_code: str | None) -> TraceContext:
        return self._bind(device_code=non_empty_str(device_code) or self.device_code)

    def with_command(self, command: Any) -> TraceContext:
        return self._bind(
            command_id=_attr_int(command, "id") or self.command_id,
            command_code=_attr_str(command, "command_code") or self.command_code,
            trace_id=_attr_str(command, "trace_id") or self.trace_id,
            workline_id=_attr_int(command, "workline_id") or self.workline_id,
            device_id=_attr_int(command, "device_id") or self.device_id,
            plugin_key=_attr_str(command, "plugin_key") or self.plugin_key,
            contract_version=_attr_str(command, "contract_version") or self.contract_version,
        )

    def with_command_code(self, command_code: str | None) -> TraceContext:
        return self._bind(command_code=non_empty_str(command_code) or self.command_code)

    def with_outbox(self, outbox: Any) -> TraceContext:
        return self._bind(
            outbox_id=_attr_int(outbox, "id") or self.outbox_id,
            dispatch_key=_attr_str(outbox, "dispatch_key") or self.dispatch_key,
            workline_id=_attr_int(outbox, "workline_id") or self.workline_id,
            session_id=_attr_int(outbox, "session_id") or self.session_id,
        )

    def with_dispatch_key(self, dispatch_key: str | None) -> TraceContext:
        return self._bind(dispatch_key=non_empty_str(dispatch_key) or self.dispatch_key)

    def with_canonical_event_type(self, canonical_event_type: str | None) -> TraceContext:
        return self._bind(canonical_event_type=non_empty_str(canonical_event_type) or self.canonical_event_type)

    def with_transition(self, transition: str | None) -> TraceContext:
        return self._bind(transition=non_empty_str(transition) or self.transition)

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
            target_code = target_code or _attr_str(outbox, "target_code")
        payload: dict[str, Any] = {
            "outbox_id": self.outbox_id or _attr_int(outbox, "id"),
            "dispatch_key": self.dispatch_key or _attr_str(outbox, "dispatch_key"),
            "dispatch_type": dispatch_type,
            "target_code": target_code,
        }
        payload.update({key: value for key, value in extra.items() if value is not None})
        return payload

    def as_dict(self) -> dict[str, Any]:
        """返回适合日志 / 调试输出的紧凑字典。"""

        return {key: value for key, value in asdict(self).items() if value is not None}


__all__ = ["TraceContext"]
