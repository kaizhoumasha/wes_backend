"""Workline 诊断事件支撑函数。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.consumers.diagnostics_bridge import (
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
)
from src.app.workline.trace_context import TraceContext
from src.app.workline.utils import payload_dict
from src.core.logger import logger
from src.utils.value_normalization import canonical_event_type


def _log_diagnostic(
    *,
    inbox: Any | None,
    error_code: ErrorCode,
    message: str,
    error_domain: ErrorDomain | None = None,
    problem_class: ProblemClass | None = None,
    session: Any | None = None,
    workline: Any | None = None,
    device: Any | None = None,
    command: Any | None = None,
    outbox: Any | None = None,
    transition: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    payload = payload_dict(getattr(inbox, "payload_json", None)) if inbox is not None else {}
    trace = TraceContext.from_runtime(
        session=session,
        workline=workline,
        inbox=inbox,
        command=command,
        outbox=outbox,
        request_id=request_id,
        trace_id=getattr(inbox, "trace_id", None) or getattr(session, "trace_id", None) or trace_id,
        canonical_event_type=canonical_event_type(payload),
        transition=transition,
    )
    if device is not None:
        trace = trace.with_device(device)
    event = build_diagnostic_event(
        error_code=error_code,
        context=build_diagnostic_context(
            trace=trace,
            session=session,
            inbox=inbox,
            command=command,
            device=device,
            outbox=outbox,
            workline=workline,
            request_id=request_id,
            trace_id=trace_id,
            canonical_event_type=trace.canonical_event_type,
            transition=transition,
            extra=extra,
        ),
        message=message,
        error_domain=error_domain,
        problem_class=problem_class,
        technical_summary=message,
    )
    card = build_diagnostic_card(event)
    logger.warning(f"[WorklineDiagnostic] {card.model_dump_json(exclude_none=True)}")
    return event


async def _record_diagnostic(db: Any, **kwargs: Any) -> None:
    """记录诊断日志并尽力持久化诊断卡片。"""
    from src.app.workline.services.diagnostic_service import workline_diagnostic_service

    try:
        event = _log_diagnostic(**kwargs)
    except Exception as exc:
        fallback_context = {
            "error_code": getattr(kwargs.get("error_code"), "value", kwargs.get("error_code")),
            "message": kwargs.get("message"),
            "trace_id": kwargs.get("trace_id")
            or getattr(kwargs.get("inbox"), "trace_id", None)
            or getattr(kwargs.get("session"), "trace_id", None),
            "inbox_id": getattr(kwargs.get("inbox"), "id", None),
            "outbox_id": getattr(kwargs.get("outbox"), "id", None),
        }
        logger.opt(exception=True).warning("工作线诊断构造失败: {}; fallback_context={}", exc, fallback_context)
        return
    try:
        _ = await workline_diagnostic_service.record_event(
            db,
            event=event,
            evidence=kwargs.get("extra"),
            auto_commit=False,
        )
    except Exception as exc:
        logger.warning(f"工作线诊断持久化失败: {exc}")


__all__ = ["_log_diagnostic", "_record_diagnostic"]
