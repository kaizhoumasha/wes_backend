# 旧 plugin runtime 镜像实现:src.workline_runtime.session_resolver 的平级副本
# 旧 runtime 入口删除后,本模块承载对应正式实现。
# 自引用 src.workline_runtime.{business_identity, run_mode}
# 已重定向到 business identity bridge + stable workline run_mode。

"""
Session 归属解析器

根据 Inbox 类型和归属规则解析或创建 Session。

规则（按 RuntimeInbox.kind）:
- EXTERNAL_HTTP: 优先按 dispatch_key -> outbox/session 恢复 Session，回退 trace_id
- INTERNAL_EVENT / REPLAY_REQUEST / MANUAL_*: 按 session_id 恢复 Session

设计参考: runtime-orchestration 设计文档
"""

from typing import Any, NotRequired, TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.workline.trace_context import TraceContext
from src.app.workline.utils import ensure_dict, non_empty_str

_SESSION_ID_KINDS = {
    "MANUAL_HOLD",
    "MANUAL_RESUME",
    "MANUAL_CANCEL",
    "REPLAY_REQUEST",
    "INTERNAL_EVENT",
}

# 待处理 ingress 元数据属性名。
_PENDING_SESSION_INGRESS_METADATA_ATTR = "_pending_session_ingress_metadata"


class SessionResolveError(ValueError):
    """Session 归属解析失败。"""


class SessionIngressMetadata(TypedDict):
    """复用 session 时的 ingress 元数据补丁。"""

    ingress_count: int
    last_ingress_at: Any
    last_request_id: NotRequired[str]
    trace_id: NotRequired[str]


def _build_session_ingress_metadata(
    session: WorklineSession,
    *,
    trace: TraceContext,
    observed_at: Any,
) -> SessionIngressMetadata:
    """为复用已有 session 构造最终应持久化的 ingress 元数据。"""

    current = getattr(session, "ingress_count", None)
    ingress_count = current + 1 if isinstance(current, int) and current >= 1 else 2
    metadata: SessionIngressMetadata = {
        "ingress_count": ingress_count,
        "last_ingress_at": observed_at,
    }
    if trace.request_id:
        metadata["last_request_id"] = trace.request_id
    if not non_empty_str(getattr(session, "trace_id", None)) and trace.trace_id:
        metadata["trace_id"] = trace.trace_id
    return metadata


def _apply_session_ingress_metadata(session: WorklineSession, metadata: SessionIngressMetadata) -> None:
    """把 ingress 元数据应用到 session 对象。"""

    session.ingress_count = metadata["ingress_count"]
    if "last_request_id" in metadata:
        session.last_request_id = metadata["last_request_id"]
    session.last_ingress_at = metadata["last_ingress_at"]
    if "trace_id" in metadata:
        session.trace_id = metadata["trace_id"]


def _stash_pending_session_ingress_metadata(session: WorklineSession, metadata: SessionIngressMetadata) -> None:
    """暂存复用 session 的 ingress 补丁，供锁内 refresh 后重放。"""

    setattr(session, _PENDING_SESSION_INGRESS_METADATA_ATTR, dict(metadata))


def reapply_pending_session_ingress_metadata(session: WorklineSession) -> bool:
    """在 refresh() 之后重放复用 session 的 ingress 元数据补丁。"""

    metadata = getattr(session, _PENDING_SESSION_INGRESS_METADATA_ATTR, None)
    if not isinstance(metadata, dict) or not metadata:
        return False

    _apply_session_ingress_metadata(session, cast("SessionIngressMetadata", metadata))
    return True


def _bind_reused_session_to_inbox(inbox: Any, session: WorklineSession) -> None:
    """复用已有 session 时，把 inbox trace 锚点对齐到 session 主链。"""
    session_trace_id = getattr(session, "trace_id", None)
    if isinstance(session_trace_id, str) and session_trace_id:
        inbox.trace_id = session_trace_id


def _reuse_existing_session(
    inbox: Any,
    session: WorklineSession,
    *,
    trace: TraceContext,
    observed_at: Any,
) -> WorklineSession:
    ingress_metadata = _build_session_ingress_metadata(session, trace=trace, observed_at=observed_at)
    _apply_session_ingress_metadata(session, ingress_metadata)
    _stash_pending_session_ingress_metadata(session, ingress_metadata)
    _bind_reused_session_to_inbox(inbox, session)
    return session


class SessionResolver:
    """Session 归属解析器

    根据 Inbox 和归属规则解析或创建 Session。

    属性:
        session_repo: Session 仓库实例
    """

    def __init__(
        self,
        session_repo: WorklineSessionRepository | None = None,
        workline_repo: Any | None = None,
        outbox_repo: SystemOutboxRepository | None = None,
    ) -> None:
        """初始化 SessionResolver

        Args:
            session_repo: Session 仓库实例（可选，默认使用全局单例）
        """
        self.session_repo = session_repo or workline_session_repository
        if workline_repo is None:
            from src.app.runtime.orchestration.repository_wiring import workline_repository

            workline_repo = workline_repository
        self.workline_repo = workline_repo
        self.outbox_repo = outbox_repo or system_outbox_repository

    async def resolve_or_create(
        self,
        db: AsyncSession,
        inbox: Any,
        *,
        session_id: int | None,
    ) -> WorklineSession:
        """根据 Inbox 和归属规则解析或创建 Session

        Args:
            db: 数据库会话
            inbox: 收件箱消息
        Returns:
            解析或创建的 Session

        Raises:
            ValueError: 当必需的关联字段缺失或 Session 不存在时
        """
        kind = inbox.kind

        if kind == "EXTERNAL_HTTP":
            return await self._resolve_external_http(db, inbox)
        if kind in _SESSION_ID_KINDS:
            return await self._resolve_by_session_id(db, inbox, session_id=session_id)
        raise ValueError(f"Unsupported RuntimeInbox kind: {kind}")

    async def _resolve_external_http(
        self,
        db: AsyncSession,
        inbox: Any,
    ) -> WorklineSession:
        """处理 EXTERNAL_HTTP 类型的 Session 解析

        优先按 dispatch_key 找到 outbox/session，找不到时回退 trace_id。

        Args:
            db: 数据库会话
            inbox: 收件箱消息

        Returns:
            解析的 Session

        Raises:
            ValueError: 当 trace_id 缺失或 Session 不存在时
        """
        trace = TraceContext.from_runtime(inbox=inbox)
        trace_id = trace.trace_id
        payload_json = ensure_dict(inbox.payload_json)
        dispatch_key = non_empty_str(payload_json.get("dispatch_key"))

        if dispatch_key is not None:
            outbox = await self.outbox_repo.get_by_dispatch_key(db, dispatch_key)
            session_id = getattr(outbox, "session_id", None) if outbox is not None else None
            if isinstance(session_id, int):
                session_by_dispatch_key = await self.session_repo.get_by_id(db, session_id)
                if session_by_dispatch_key is not None:
                    inbox.workline_id = getattr(session_by_dispatch_key, "workline_id", None)
                    return session_by_dispatch_key

        if not trace_id:
            raise ValueError("trace_id is required for EXTERNAL_HTTP")

        # 按 trace_id 查找 Session
        session = await self.session_repo.get_by_trace_id(
            db=db,
            trace_id=trace_id,
        )

        if not session:
            raise ValueError(f"Session not found for trace_id: {trace_id}")

        inbox.workline_id = getattr(session, "workline_id", None)
        return session

    async def _resolve_by_session_id(
        self,
        db: AsyncSession,
        inbox: Any,
        *,
        session_id: int | None,
    ) -> WorklineSession:
        """按 session_id 恢复 Session

        用于 MANUAL_*、REPLAY_REQUEST、INTERNAL_EVENT 类型。

        Args:
            db: 数据库会话
            inbox: 收件箱消息

        Returns:
            解析的 Session

        Raises:
            ValueError: 当 session_id 缺失或 Session 不存在时
        """
        if not session_id:
            raise ValueError(f"session_id is required for {inbox.kind}")

        session = await self.session_repo.get_by_id(db, session_id)

        if not session:
            raise ValueError(f"Session not found: {session_id}")

        if inbox.kind == "INTERNAL_EVENT":
            inbox_workline_id = getattr(inbox, "workline_id", None)
            session_workline_id = getattr(session, "workline_id", None)
            if (
                isinstance(inbox_workline_id, int)
                and isinstance(session_workline_id, int)
                and inbox_workline_id != session_workline_id
            ):
                raise ValueError(
                    "INTERNAL_EVENT workline_id mismatch: "
                    f"inbox.workline_id={inbox_workline_id}, session.workline_id={session_workline_id}"
                )
            if not isinstance(inbox_workline_id, int) and isinstance(session_workline_id, int):
                inbox.workline_id = session_workline_id

        return session


# 创建单例
session_resolver = SessionResolver()


__all__ = [
    "SessionResolveError",
    "SessionResolver",
    "reapply_pending_session_ingress_metadata",
    "session_resolver",
]
