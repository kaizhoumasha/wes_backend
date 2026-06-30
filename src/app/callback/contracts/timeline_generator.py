"""Callback 域 timeline 生成器 — wlr.timeline_generator 镜像 (Phase 2 launch PR)。

镜像说明:
- TimelineGenerator 与 wlr.timeline_generator.TimelineGenerator 行为一致,
  公开方法签名 `generate(session, stage, action_type, ...)` 与 wlr 等价。
- Timeline 模型继续引用 src.app.workline.models.timeline (callback 域合法
  依赖,不构成跨域)。
- timeline_generator 单例保持 callback 域可见。
"""

from __future__ import annotations

from typing import Any, Protocol

from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.utils.timezone import timezone

from .trace_context import TraceContext


class SessionLike(Protocol):
    id: int
    workline_id: int
    trace_id: str | None


class TimelineGenerator:
    """根据 Session/TraceContext 生成统一 Timeline 记录。

    seq_no 由 AtomicWriter 从数据库序列获取,此处不设置。
    """

    def generate(
        self,
        session: SessionLike,
        stage: TimelineStage,
        action_type: TimelineActionType,
        payload: dict[str, Any] | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        actor_type: TimelineActorType | None = None,
        actor_code: str | None = None,
        message: str | None = None,
        related_inbox_id: int | None = None,
        related_command_id: int | None = None,
        status: TimelineStatus | None = None,
        failure_domain: str | None = None,
        trace: TraceContext | None = None,
    ) -> WorklineTimeline:
        resolved_trace = trace or TraceContext.from_runtime(session=session)

        return WorklineTimeline(
            session_id=resolved_trace.session_id or session.id,
            workline_id=resolved_trace.workline_id or session.workline_id,
            trace_id=resolved_trace.trace_id or session.trace_id,
            seq_no=0,
            occurred_at=timezone.now_for_db(),
            stage=stage,
            action_type=action_type,
            actor_type=actor_type or TimelineActorType.ORCHESTRATOR,
            actor_code=actor_code,
            from_status=from_status,
            to_status=to_status,
            status=status or TimelineStatus.SUCCESS,
            failure_domain=failure_domain,
            message=message,
            payload_json=payload,
            related_inbox_id=related_inbox_id,
            related_command_id=related_command_id,
        )


timeline_generator = TimelineGenerator()


__all__ = ["SessionLike", "TimelineGenerator", "timeline_generator"]
