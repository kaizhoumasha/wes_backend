# 阶段 2 burn-down C5b 镜像:src.workline_runtime.timeline_generator 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像改名为正式模块。
# 自引用 src.workline_runtime.trace_context 已重定向到 C2 镜像。

"""
TimelineGenerator - 时间线记录生成器

负责从 Session 提取关联信息并生成 Timeline 记录。
seq_no 由 AtomicWriter 从数据库序列获取。

设计参考: 设计文档 phase2-orchestrator
"""

from typing import Any, Protocol

from src.app.workline.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.workline.trace_context import TraceContext
from src.utils.timezone import timezone


class SessionLike(Protocol):
    id: int
    workline_id: int
    trace_id: str | None


class TimelineGenerator:
    """时间线记录生成器

    从 Session 提取关联信息，生成 Timeline 记录对象。
    注意：seq_no 由 AtomicWriter 从数据库序列获取，此处不设置。
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
        """生成 Timeline 记录

        Args:
            session: WorklineSession 对象，提取 session_id, workline_id, trace_id
            stage: 阶段
            action_type: 动作类型
            payload: 负载数据
            from_status: 原状态
            to_status: 目标状态
            actor_type: 参与者类型（默认为 ORCHESTRATOR）
            actor_code: 参与者编码
            message: 消息
            related_inbox_id: 关联的 Inbox ID
            related_command_id: 关联的设备指令 ID
            status: 条目状态（默认为 SUCCESS）
            failure_domain: 失败域

        Returns:
            WorklineTimeline 记录对象（未持久化）
        """
        resolved_trace = trace or TraceContext.from_runtime(session=session)

        return WorklineTimeline(
            session_id=resolved_trace.session_id or session.id,
            workline_id=resolved_trace.workline_id or session.workline_id,
            trace_id=resolved_trace.trace_id or session.trace_id,
            seq_no=0,  # 由 AtomicWriter 从数据库序列获取并替换
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


# 单例
timeline_generator = TimelineGenerator()
