# 旧 plugin runtime 镜像实现:src.workline_runtime.session_resolver 的平级副本
# 旧 runtime 入口删除后,本模块承载对应正式实现。
# 自引用 src.workline_runtime.{business_identity, run_mode}
# 已重定向到 business identity bridge + stable workline run_mode。

"""
Session 归属解析器

根据 Inbox 类型和归属规则解析或创建 Session。

规则（按 RuntimeInbox.kind）:
- DEVICE_EVENT: 按 device_id + business_key 查找或创建
- COMMAND_RESULT: 按持久 command_id 或显式 execution correlation 恢复 Session
- EXTERNAL_HTTP: 优先按 dispatch_key -> outbox/session 恢复 Session，回退 trace_id
- INTERNAL_EVENT: 按 session_id 恢复 Session
- TIMER_TIMEOUT: 按 session_id 恢复 Session
- MANUAL_*: 按 session_id 恢复 Session

设计参考: runtime-orchestration 设计文档
"""

import uuid
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.repositories.command_repository import DeviceCommandRepository
from src.app.runtime.orchestration.business_identity_bridge import resolve_payload_display_identity
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.workline.domain.run_mode import normalize_run_mode
from src.app.workline.trace_context import TraceContext
from src.app.workline.utils import ensure_dict, non_empty_str
from src.database.dialect import dialect_name
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.workline.models.workline import WorkLine

_SESSION_ID_KINDS = {
    "TIMER_TIMEOUT",
    "MANUAL_HOLD",
    "MANUAL_RESUME",
    "MANUAL_CANCEL",
    "REPLAY_REQUEST",
    "INTERNAL_EVENT",
}

# 无业务条码、但每次事件实例都必须独立归属的事件。
_EVENT_INSTANCE_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "MATERIAL_ARRIVED": ("event_id", "vendor_event_id"),
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


def _resolve_event_scope_business_key(payload_json: dict[str, Any]) -> str | None:
    """为无业务条码的设备级事件生成稳定归属键。"""

    event_type = payload_json.get("canonical_event_type") or payload_json.get("event_type")
    device_code = payload_json.get("device_code")
    if not isinstance(event_type, str) or not event_type:
        return None
    if not isinstance(device_code, str) or not device_code:
        return None

    data = ensure_dict(payload_json.get("data"))
    for field_name in _EVENT_INSTANCE_IDENTITY_FIELDS.get(event_type, ()):
        event_identity = data.get(field_name)
        if isinstance(event_identity, str) and event_identity:
            return f"event:{event_type}:{device_code}:{event_identity}"

    return None


def _resolve_payload_barcode(data: dict[str, Any]) -> str | None:
    """从 `data` 中提取稳定单字段条码。

    注意：白皮书已禁止拍平 payload，只从嵌套 data 结构提取。
    """

    return non_empty_str(data.get("barcode"))


async def _lock_device_event_business_keys(db: Any, *, workline_id: int, business_keys: set[str]) -> None:
    """按实际 64-bit advisory resource 顺序锁定业务键的查找与创建窗口。"""

    if dialect_name(db) != "postgresql" or not business_keys:
        return

    lock_keys = [f"workline-session:{workline_id}:{business_key}" for business_key in sorted(business_keys)]
    await db.execute(
        text(
            """
            WITH lock_ids AS MATERIALIZED (
                SELECT DISTINCT hashtextextended(input.lock_key, 0) AS lock_id
                FROM unnest(CAST(:lock_keys AS text[])) AS input(lock_key)
            ),
            ordered_lock_ids AS MATERIALIZED (
                SELECT lock_id
                FROM lock_ids
                ORDER BY lock_id
            )
            SELECT pg_advisory_xact_lock(lock_id)
            FROM ordered_lock_ids
            ORDER BY lock_id
            """
        ),
        {"lock_keys": lock_keys},
    )


def _resolve_business_key(
    payload_json: dict[str, Any],
) -> str:
    """从事件 payload 提取业务主键，无法稳定求值时显式失败。

    明确允许的设备级事件按 event_type + device_code 稳定归属。
    缺少稳定业务标识时显式失败，避免重复建单。
    """
    data = ensure_dict(payload_json.get("data"))
    business_key = payload_json.get("business_key")
    if isinstance(business_key, str) and business_key:
        return business_key

    event_scoped_business_key = _resolve_event_scope_business_key(payload_json)
    if event_scoped_business_key:
        return event_scoped_business_key

    barcode = _resolve_payload_barcode(data)
    if barcode:
        return barcode

    raise SessionResolveError(
        "Unable to resolve stable business_key from payload: missing business_key, barcode, and event identity"
    )


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
        command_repo: DeviceCommandRepository | None = None,
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
        self.command_repo = command_repo or DeviceCommandRepository()
        self.outbox_repo = outbox_repo or system_outbox_repository

    async def resolve_or_create(
        self,
        db: AsyncSession,
        inbox: Any,
        workline: "WorkLine | None",
        devices_by_role: dict[str, list[Any]],
        *,
        session_id: int | None,
    ) -> WorklineSession:
        """根据 Inbox 和归属规则解析或创建 Session

        Args:
            db: 数据库会话
            inbox: 收件箱消息
            workline: 作业线
            devices_by_role: 设备角色映射

        Returns:
            解析或创建的 Session

        Raises:
            ValueError: 当必需的关联字段缺失或 Session 不存在时
        """
        _ = devices_by_role
        kind = inbox.kind

        if kind == "DEVICE_EVENT":
            if workline is None:
                raise ValueError("workline is required for DEVICE_EVENT")
            return await self._resolve_device_event(db, inbox, workline)
        if kind == "COMMAND_RESULT":
            return await self._resolve_command_result(db, inbox)
        if kind == "EXTERNAL_HTTP":
            return await self._resolve_external_http(db, inbox)
        if kind in _SESSION_ID_KINDS:
            return await self._resolve_by_session_id(db, inbox, session_id=session_id)
        raise ValueError(f"Unsupported RuntimeInbox kind: {kind}")

    async def _reuse_existing_device_event_session(
        self,
        db: AsyncSession,
        inbox: Any,
        *,
        workline_id: int,
        business_key: str,
        trace: TraceContext,
        observed_at: Any,
    ) -> WorklineSession | None:
        """探测并在 advisory lock 内复查、复用既有业务周期。"""
        existing_session = await self._find_existing_device_event_session(
            db,
            inbox,
            workline_id=workline_id,
            business_key=business_key,
            trace=trace,
            observed_at=observed_at,
            apply_ingress_metadata=False,
        )
        if existing_session is None:
            return None

        locked_business_keys = {business_key}
        persisted_business_key = non_empty_str(getattr(existing_session, "business_key", None))
        if persisted_business_key is not None:
            locked_business_keys.add(persisted_business_key)
        await _lock_device_event_business_keys(
            db,
            workline_id=workline_id,
            business_keys=locked_business_keys,
        )
        locked_existing_session = await self._find_existing_device_event_session(
            db,
            inbox,
            workline_id=workline_id,
            business_key=business_key,
            trace=trace,
            observed_at=observed_at,
        )
        if locked_existing_session is None:
            raise SessionResolveError("Session ownership changed while acquiring business key lock")
        locked_session_business_key = non_empty_str(getattr(locked_existing_session, "business_key", None))
        if locked_session_business_key is not None and locked_session_business_key not in locked_business_keys:
            raise SessionResolveError("Session business key changed outside the acquired advisory lock set")
        return locked_existing_session

    async def _resolve_device_event(
        self,
        db: AsyncSession,
        inbox: Any,
        workline: "WorkLine",
    ) -> WorklineSession:
        """处理 DEVICE_EVENT 类型的 Session 解析

        按 device_id + business_key 查找或创建 Session。

        Args:
            db: 数据库会话
            inbox: 收件箱消息
            workline: 作业线

        Returns:
            解析或创建的 Session
        """
        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            raise TypeError("workline.id is required for DEVICE_EVENT")

        payload_json = ensure_dict(inbox.payload_json)
        business_key = _resolve_business_key(payload_json)
        now = timezone.now_for_db()
        trace = TraceContext.from_runtime(inbox=inbox, workline=workline)

        existing_session = await self._reuse_existing_device_event_session(
            db,
            inbox,
            workline_id=workline_id,
            business_key=business_key,
            trace=trace,
            observed_at=now,
        )
        if existing_session is not None:
            return existing_session

        current_workline = await self.workline_repo.get_by_id(db, workline_id)
        if current_workline is None:
            raise ValueError(f"WorkLine not found: {workline_id}")
        workline = current_workline
        if not bool(getattr(workline, "is_active", False)):
            from src.app.workline.services.safety_service import WorkLineSafetyBlocked

            raise WorkLineSafetyBlocked(f"WorkLine 已停用，不再接收新工作: workline_id={workline_id}")
        trace = TraceContext.from_runtime(inbox=inbox, workline=workline)
        await _lock_device_event_business_keys(
            db,
            workline_id=workline_id,
            business_keys={business_key},
        )
        existing_session = await self._find_existing_device_event_session(
            db,
            inbox,
            workline_id=workline_id,
            business_key=business_key,
            trace=trace,
            observed_at=now,
        )
        if existing_session is not None:
            return existing_session

        # 创建新 Session
        session_code = f"SES_{uuid.uuid4().hex[:16]}"

        trace_id = trace.trace_id or f"trace_{uuid.uuid4().hex}"
        inbox.trace_id = trace_id
        session_data: dict[str, Any] = {
            "session_code": session_code,
            "workline_id": workline_id,
            "run_mode": RunMode(normalize_run_mode(getattr(workline, "run_mode", None))),
            "business_key": business_key,
            "barcode": resolve_payload_display_identity(payload_json),
            "status": SessionStatus.NEW,
            "ingress_count": 1,
            "last_request_id": trace.request_id,
            "last_ingress_at": now,
            "trace_id": trace_id,
            "context_json": {
                "device_id": inbox.device_id,
                "source_message_id": trace.request_id,
                "initial_payload": payload_json,
            },
            "started_at": now,
        }

        new_session = await self.session_repo.create(db, session_data)
        if new_session is None:
            raise RuntimeError("Failed to create session for DEVICE_EVENT")

        return new_session

    async def _find_existing_device_event_session(
        self,
        db: AsyncSession,
        inbox: Any,
        *,
        workline_id: int,
        business_key: str,
        trace: TraceContext,
        observed_at: Any,
        apply_ingress_metadata: bool = True,
    ) -> WorklineSession | None:
        """查找并复用既有业务周期；该快路径无需读取或锁定当前 WorkLine pin。"""

        # 1. 优先查找未结束的 Session
        existing_session = await self.session_repo.get_open_session_by_business_key(
            db=db,
            workline_id=workline_id,
            business_key=business_key,
            populate_existing=apply_ingress_metadata,
        )

        if existing_session:
            return (
                _reuse_existing_session(inbox, existing_session, trace=trace, observed_at=observed_at)
                if apply_ingress_metadata
                else existing_session
            )

        # 2. 如果没有未结束的 Session，尝试通过 trace_id 查找
        if trace.trace_id:
            session_by_trace = await self.session_repo.get_by_trace_id(
                db=db,
                trace_id=trace.trace_id,
                populate_existing=apply_ingress_metadata,
            )
            if session_by_trace and getattr(session_by_trace, "workline_id", None) == workline_id:
                return (
                    _reuse_existing_session(inbox, session_by_trace, trace=trace, observed_at=observed_at)
                    if apply_ingress_metadata
                    else session_by_trace
                )

        # 3. 如果还是没有，查找最新的 Session。
        #    同一 business_key 的入口事件是否允许开启新周期，不能由“完成后几秒”决定；
        #    在没有显式 rework/新物料授权前，终态 session 仍是该物料周期的归属锚点。
        latest_session = await self.session_repo.get_latest_session_by_business_key(
            db=db,
            workline_id=workline_id,
            business_key=business_key,
            populate_existing=apply_ingress_metadata,
        )

        if latest_session and latest_session.ended_at:
            return (
                _reuse_existing_session(inbox, latest_session, trace=trace, observed_at=observed_at)
                if apply_ingress_metadata
                else latest_session
            )
        return None

    async def _resolve_command_result(
        self,
        db: AsyncSession,
        inbox: Any,
    ) -> WorklineSession:
        """处理 COMMAND_RESULT 类型的 Session 解析。"""
        command_id = getattr(inbox, "command_id", None)
        command = (
            await self.command_repo.get_by_id(db, command_id)
            if isinstance(command_id, int) and not isinstance(command_id, bool)
            else None
        )
        if command is not None:
            session = await self.session_repo.get_open_session_by_awaiting_device_command_code(db, command.command_code)
            if session is not None:
                return session

            # 已知迟到/错配指令仅可归属到同 WorkLine 的唯一开放 Session，
            # 最终 command/wait/correlation 一致性由 generated bridge fail closed。
            if command.workline_id is not None:
                open_sessions = await self.session_repo.list_open_by_workline_id(
                    db,
                    workline_id=command.workline_id,
                    limit=2,
                )
                if len(open_sessions) == 1:
                    return open_sessions[0]

        correlation_id = non_empty_str(getattr(inbox, "correlation_id", None))
        prefix = "workline-session:"
        if correlation_id is None or not correlation_id.startswith(prefix):
            raise ValueError("COMMAND_RESULT requires persisted command_id or workline session correlation")
        session_code = correlation_id.removeprefix(prefix)
        if not session_code:
            raise ValueError("COMMAND_RESULT workline session correlation is invalid")
        session = await self.session_repo.get_by_session_code(db, session_code)
        if session is None:
            raise ValueError(f"Session not found for correlation: {correlation_id}")
        inbox_workline_id = getattr(inbox, "workline_id", None)
        if isinstance(inbox_workline_id, int) and inbox_workline_id != session.workline_id:
            raise ValueError("COMMAND_RESULT correlation workline mismatch")
        return session

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

        用于 TIMER_TIMEOUT、MANUAL_*、REPLAY_REQUEST、INTERNAL_EVENT 类型。

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
