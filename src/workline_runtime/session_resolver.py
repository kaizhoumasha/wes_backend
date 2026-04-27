"""
Session 归属解析器

根据 Inbox 类型和归属规则解析或创建 Session。

规则（按 InboxKind）:
- DEVICE_EVENT: 按 device_id + business_key 查找或创建
- COMMAND_RESULT: 按 command_code -> awaiting_command_id / trace_id 恢复 Session
- EXTERNAL_HTTP: 按 trace_id 恢复 Session
- TIMER_TIMEOUT: 按 session_id 恢复 Session
- MANUAL_*: 按 session_id 恢复 Session

设计参考: 设计文档 phase2-orchestrator
"""

import uuid
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.repositories.command_repository import DeviceCommandRepository
from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.utils.timezone import timezone
from src.workline_plugin_registry import (
    get_plugin_contract_version,
    resolve_workline_business_key,
)
from src.workline_runtime.run_mode import normalize_run_mode

from .trace_context import TraceContext
from .utils import ensure_dict, non_empty_str

if TYPE_CHECKING:
    from src.app.workline.models.inbox import WorklineInbox
    from src.app.workline.models.workline import WorkLine

_SESSION_ID_KINDS = {
    InboxKind.TIMER_TIMEOUT,
    InboxKind.MANUAL_HOLD,
    InboxKind.MANUAL_RESUME,
    InboxKind.MANUAL_CANCEL,
    InboxKind.REPLAY_REQUEST,
}

# 无业务条码但可按设备级单例归属的事件。
_DEVICE_SCOPED_EVENTS = {
    "ESTOP_PRESSED",
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

    if event_type in _DEVICE_SCOPED_EVENTS:
        return f"event:{event_type}:{device_code}"

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


def _resolve_plugin_business_key(payload_json: dict[str, Any], *, plugin_key: str | None) -> str | None:
    """通过插件 manifest 恢复稳定 business_key。"""

    try:
        return resolve_workline_business_key(plugin_key, payload_json)
    except (TypeError, ValueError) as exc:
        raise SessionResolveError(f"Plugin business_key resolver failed: {exc}") from exc


def _resolve_business_key(payload_json: dict[str, Any], *, plugin_key: str | None = None) -> str:
    """从事件 payload 提取业务主键，无法稳定求值时显式失败。

    约束：
    - 原始外部协议字段映射优先走插件 manifest 的 business_key_resolver
    - 明确允许的设备级事件（如 ESTOP）按 event_type + device_code 稳定归属
    - 对未知插件且缺少稳定业务标识的 payload，不再返回随机 business_key，
      而是显式抛出 SessionResolveError，避免重复建单
    """
    data = ensure_dict(payload_json.get("data"))
    # 插件解析器优先级最高。SMT 等插件可在这里按自身 data 模型派生业务键，
    # 不再把供应商字段名固化到通用 SessionResolver。
    business_key_from_plugin = _resolve_plugin_business_key(payload_json, plugin_key=plugin_key)
    if business_key_from_plugin:
        return business_key_from_plugin

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
        "Unable to resolve stable business_key from payload: missing plugin business key, business_key, barcode, and event identity"
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


def _bind_reused_session_to_inbox(inbox: "WorklineInbox", session: WorklineSession) -> None:
    """复用已有 session 时，把 inbox trace 锚点对齐到 session 主链。"""

    session_id = getattr(session, "id", None)
    if isinstance(session_id, int):
        inbox.session_id = session_id

    session_trace_id = getattr(session, "trace_id", None)
    if isinstance(session_trace_id, str) and session_trace_id:
        inbox.trace_id = session_trace_id


def _reuse_existing_session(
    inbox: "WorklineInbox",
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


def _resolve_workline_contract_version(workline: "WorkLine | None") -> str | None:
    """解析运行时 contract_version，优先 workline 快照，缺失时回退 registry。"""

    workline_contract_version = getattr(workline, "contract_version", None)
    if isinstance(workline_contract_version, str) and workline_contract_version:
        return workline_contract_version

    plugin_key = getattr(workline, "plugin_key", None)
    contract_version = get_plugin_contract_version(plugin_key)
    return contract_version if isinstance(contract_version, str) and contract_version else None


class SessionResolver:
    """Session 归属解析器

    根据 Inbox 和归属规则解析或创建 Session。

    属性:
        session_repo: Session 仓库实例
    """

    def __init__(
        self,
        session_repo: WorklineSessionRepository | None = None,
        command_repo: DeviceCommandRepository | None = None,
    ) -> None:
        """初始化 SessionResolver

        Args:
            session_repo: Session 仓库实例（可选，默认使用全局单例）
        """
        self.session_repo = session_repo or workline_session_repository
        self.command_repo = command_repo or DeviceCommandRepository()

    async def resolve_or_create(
        self,
        db: AsyncSession,
        inbox: "WorklineInbox",
        workline: "WorkLine | None",
        devices_by_role: dict[str, list[Any]],
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

        if kind == InboxKind.DEVICE_EVENT:
            if workline is None:
                raise ValueError("workline is required for DEVICE_EVENT")
            return await self._resolve_device_event(db, inbox, workline)
        if kind == InboxKind.COMMAND_RESULT:
            return await self._resolve_command_result(db, inbox)
        if kind == InboxKind.EXTERNAL_HTTP:
            return await self._resolve_external_http(db, inbox)
        if kind in _SESSION_ID_KINDS:
            return await self._resolve_by_session_id(db, inbox)
        raise ValueError(f"Unsupported InboxKind: {kind}")

    async def _resolve_device_event(
        self,
        db: AsyncSession,
        inbox: "WorklineInbox",
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
        payload_json = ensure_dict(inbox.payload_json)
        business_key = _resolve_business_key(payload_json, plugin_key=getattr(workline, "plugin_key", None))
        now = timezone.now_for_db()
        trace = TraceContext.from_runtime(inbox=inbox, workline=workline)

        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            raise TypeError("workline.id is required for DEVICE_EVENT")

        # 1. 优先查找未结束的 Session
        existing_session = await self.session_repo.get_open_session_by_business_key(
            db=db,
            workline_id=workline_id,
            business_key=business_key,
        )

        if existing_session:
            return _reuse_existing_session(inbox, existing_session, trace=trace, observed_at=now)

        # 2. 如果没有未结束的 Session，尝试通过 trace_id 查找
        if trace.trace_id:
            session_by_trace = await self.session_repo.get_by_trace_id(
                db=db,
                trace_id=trace.trace_id,
            )
            if session_by_trace:
                return _reuse_existing_session(inbox, session_by_trace, trace=trace, observed_at=now)

        # 3. 如果还是没有，查找最新的 Session（处理事件在 session 完成后立即到达的情况）
        latest_session = await self.session_repo.get_latest_session_by_business_key(
            db=db,
            workline_id=workline_id,
            business_key=business_key,
        )

        if latest_session and latest_session.ended_at:
            # 如果最新的 session 刚完成不久（5秒内），继续使用它
            elapsed = (now - latest_session.ended_at).total_seconds()
            if elapsed < 5:
                return _reuse_existing_session(inbox, latest_session, trace=trace, observed_at=now)

        # 创建新 Session
        session_code = f"SES_{uuid.uuid4().hex[:16]}"

        trace_id = trace.trace_id or f"trace_{uuid.uuid4().hex}"
        inbox.trace_id = trace_id
        session_data: dict[str, Any] = {
            "session_code": session_code,
            "workline_id": workline_id,
            "plugin_key": getattr(workline, "plugin_key", None),
            "run_mode": RunMode(normalize_run_mode(getattr(workline, "run_mode", None))),
            "business_key": business_key,
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

        contract_version = _resolve_workline_contract_version(workline)
        if contract_version:
            session_data["contract_version"] = contract_version

        new_session = await self.session_repo.create(db, session_data)
        if new_session is None:
            raise RuntimeError("Failed to create session for DEVICE_EVENT")

        return new_session

    async def _resolve_command_result(
        self,
        db: AsyncSession,
        inbox: "WorklineInbox",
    ) -> WorklineSession:
        """处理 COMMAND_RESULT 类型的 Session 解析。"""
        payload_json = ensure_dict(inbox.payload_json)
        command_code = payload_json.get("command_code")
        if not isinstance(command_code, str) or not command_code:
            raise ValueError("command_code is required for COMMAND_RESULT")

        command = await self.command_repo.get_by_command_code(db, command_code)
        if command is None:
            raise ValueError(f"DeviceCommand not found for command_code: {command_code}")
        if command.id is None:
            raise ValueError(f"DeviceCommand id is missing for command_code: {command_code}")

        trace = TraceContext.from_runtime(inbox=inbox).with_command(command)
        inbox.command_id = trace.command_id
        inbox.device_id = trace.device_id or command.device_id
        if trace.workline_id is not None:
            inbox.workline_id = trace.workline_id
        if trace.trace_id:
            inbox.trace_id = trace.trace_id

        session = await self.session_repo.get_open_session_by_awaiting_command_id(db, command.id)
        if session:
            inbox.session_id = session.id
            return session

        if trace.trace_id:
            session = await self.session_repo.get_by_trace_id(db, trace.trace_id)
            if session:
                inbox.session_id = session.id
                return session

        raise ValueError(f"Session not found for command_code: {command_code}")

    async def _resolve_external_http(
        self,
        db: AsyncSession,
        inbox: "WorklineInbox",
    ) -> WorklineSession:
        """处理 EXTERNAL_HTTP 类型的 Session 解析

        按 trace_id 恢复 Session。

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

        if not trace_id:
            raise ValueError("trace_id is required for EXTERNAL_HTTP")

        # 按 trace_id 查找 Session
        session = await self.session_repo.get_by_trace_id(
            db=db,
            trace_id=trace_id,
        )

        if not session:
            raise ValueError(f"Session not found for trace_id: {trace_id}")

        inbox.session_id = session.id
        inbox.workline_id = getattr(session, "workline_id", None)
        return session

    async def _resolve_by_session_id(
        self,
        db: AsyncSession,
        inbox: "WorklineInbox",
    ) -> WorklineSession:
        """按 session_id 恢复 Session

        用于 TIMER_TIMEOUT、MANUAL_*、REPLAY_REQUEST 类型。

        Args:
            db: 数据库会话
            inbox: 收件箱消息

        Returns:
            解析的 Session

        Raises:
            ValueError: 当 session_id 缺失或 Session 不存在时
        """
        session_id = inbox.session_id

        if not session_id:
            raise ValueError(f"session_id is required for {inbox.kind}")

        session = await self.session_repo.get_by_id(db, session_id)

        if not session:
            raise ValueError(f"Session not found: {session_id}")

        return session


# 创建单例
session_resolver = SessionResolver()


__all__ = [
    "SessionResolveError",
    "SessionResolver",
    "reapply_pending_session_ingress_metadata",
    "session_resolver",
]
