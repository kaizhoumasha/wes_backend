"""Outbox 派发支撑函数。

本模块放在 app/workline 层，支撑 outbox dispatch 的 trace 与 run_mode，
同时避免触发 src.app.workline.services.__init__ 的 eager import。
"""

from __future__ import annotations

from typing import Any

from src.app.workline.domain.run_mode import normalize_run_mode
from src.app.workline.trace_context import TraceContext
from src.utils.value_normalization import enum_value


def _outbox_trace_extra(outbox: Any, trace: TraceContext | None = None) -> dict[str, Any]:
    """提取 Outbox 派发链路的稳定追踪字段。"""
    resolved_trace = trace.with_outbox(outbox) if trace is not None else TraceContext.from_runtime(outbox=outbox)
    return resolved_trace.project_outbox_trace(
        outbox=outbox,
        dispatch_type=enum_value(getattr(outbox, "dispatch_type", None)),
        target_code=getattr(outbox, "target_code", None),
    )


def _outbox_trace_log_suffix(outbox: Any, trace: TraceContext | None = None) -> str:
    """构造统一的 Outbox trace 日志后缀。"""
    trace_extra = _outbox_trace_extra(outbox, trace=trace)
    return (
        f"dispatch_type={trace_extra['dispatch_type']}, "
        f"dispatch_key={trace_extra['dispatch_key']}, "
        f"target_code={trace_extra['target_code']}"
    )


def _cached_outbox_session(outbox: Any) -> Any | None:
    """读取已加载的 outbox.session，避免为判断运行模式触发隐式懒加载。"""
    try:
        session = vars(outbox).get("session")
    except TypeError:
        session = getattr(outbox, "session", None)
    return session if session is not None else None


async def _resolve_outbox_run_mode(db: Any, outbox: Any) -> str:
    """按 Session 快照解析 Outbox 派发运行模式。"""
    session = _cached_outbox_session(outbox)
    run_mode = getattr(session, "run_mode", None)
    if run_mode is not None:
        return normalize_run_mode(run_mode)
    session_id = getattr(outbox, "session_id", None)
    if isinstance(session_id, int) and hasattr(db, "get"):
        from src.app.runtime.orchestration.models.session import WorklineSession

        loaded_session = await db.get(WorklineSession, session_id)
        return normalize_run_mode(getattr(loaded_session, "run_mode", None))
    return normalize_run_mode(None)


__all__ = [
    "_outbox_trace_extra",
    "_outbox_trace_log_suffix",
    "_resolve_outbox_run_mode",
]
