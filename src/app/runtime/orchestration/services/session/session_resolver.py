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
- EXTERNAL_HTTP: 优先按 dispatch_key -> rack task operation 恢复 Session，回退 outbox/trace_id
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
from src.app.rack.repositories import RackTaskRepository, rack_task_repository
from src.app.runtime.orchestration.business_identity_bridge import resolve_payload_display_identity
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.app.runtime.workline_plugins.registry import (
    get_workline_contract_version,
    resolve_workline_business_key,
)
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.workline.domain.run_mode import normalize_run_mode
from src.app.workline.trace_context import TraceContext
from src.app.workline.utils import ensure_dict, non_empty_str
from src.core.logger import logger
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


def _resolve_plugin_business_key(
    payload_json: dict[str, Any],
    *,
    plugin_key: str | None,
    contract_version: str | None = None,
) -> str | None:
    """通过 registry 插件运行时恢复稳定 business_key。"""

    try:
        return resolve_workline_business_key(plugin_key, payload_json, contract_version=contract_version)
    except (TypeError, ValueError) as exc:
        raise SessionResolveError(f"Plugin business_key resolver failed: {exc}") from exc


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
    *,
    plugin_key: str | None = None,
    contract_version: str | None = None,
) -> str:
    """从事件 payload 提取业务主键，无法稳定求值时显式失败。

    约束：
    - 原始外部协议字段映射优先走 registry 插件运行时 business_key 解析能力
    - 明确允许的设备级事件（如 ESTOP）按 event_type + device_code 稳定归属
    - 对未知插件且缺少稳定业务标识的 payload，不再返回随机 business_key，
      而是显式抛出 SessionResolveError，避免重复建单
    """
    data = ensure_dict(payload_json.get("data"))
    # registry 插件运行时解析器优先级最高。SMT 等插件可在这里按自身 data 模型派生业务键，
    # 不再把供应商字段名固化到通用 SessionResolver。
    business_key_from_plugin = _resolve_plugin_business_key(
        payload_json,
        plugin_key=plugin_key,
        contract_version=contract_version,
    )
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


def _resolve_workline_contract_version(workline: "WorkLine | None") -> str | None:
    """解析运行时 contract_version，优先 workline 快照，缺失时回退 registry。"""

    workline_contract_version = getattr(workline, "contract_version", None)
    if isinstance(workline_contract_version, str) and workline_contract_version:
        return workline_contract_version

    plugin_key = getattr(workline, "plugin_key", None)
    contract_version = get_workline_contract_version(plugin_key)
    return contract_version if isinstance(contract_version, str) and contract_version else None


def _binding_runtime_identity(binding: Any, workline: "WorkLine") -> tuple[Any, Any]:
    """优先使用已解析 binding 的运行时 identity，缺失时回退工作线快照。"""
    if binding is not None:
        return getattr(binding, "plugin_key", None), getattr(binding, "contract_version", None)
    return getattr(workline, "plugin_key", None), getattr(workline, "contract_version", None)


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
        rack_task_repo: RackTaskRepository | None = None,
        handling_step_repo: Any | None = None,
        handling_operation_repo: Any | None = None,
        plugin_binding_service: Any | None = None,
        execution_anchor_repo: Any | None = None,
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
        self.rack_task_repo = rack_task_repo or rack_task_repository
        if execution_anchor_repo is None:
            from src.app.runtime.orchestration.repositories.session_execution_anchor_repository import (
                session_execution_anchor_repository,
            )

            execution_anchor_repo = session_execution_anchor_repository
        self.execution_anchor_repo = execution_anchor_repo
        if plugin_binding_service is None:
            from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

            plugin_binding_service = workline_plugin_binding_service
        self.plugin_binding_service = plugin_binding_service
        if handling_step_repo is None or handling_operation_repo is None:
            from src.app.handling.repositories import handling_operation_repository, handling_step_repository

            self.handling_step_repo = handling_step_repo or handling_step_repository
            self.handling_operation_repo = handling_operation_repo or handling_operation_repository
        else:
            self.handling_step_repo = handling_step_repo
            self.handling_operation_repo = handling_operation_repo

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
        await self._backfill_platform_execution_anchor(db, inbox=inbox, session=locked_existing_session)
        return locked_existing_session

    async def _pin_new_device_event_execution_anchor(
        self,
        db: AsyncSession,
        *,
        inbox: Any,
        workline: "WorkLine",
        session: WorklineSession,
        binding: Any,
    ) -> None:
        """创建平台 Session 后校验并写回同事务 execution/correlation 锚点。"""
        execution_anchor = await self.plugin_binding_service.pin_new_runtime_session(
            db,
            workline=workline,
            session=session,
            binding=binding,
        )
        execution_session, work_item = execution_anchor
        execution_session_id = getattr(execution_session, "id", None)
        correlation_id = getattr(work_item, "correlation_id", None)
        if not isinstance(execution_session_id, int) or not isinstance(correlation_id, str) or not correlation_id:
            raise RuntimeError("新平台 Session 缺少持久化执行锚点")
        # Stage 3 只信任 Inbox 上的 execution/correlation 锚点；必须与聚合创建处于同一事务。
        inbox.execution_session_id = execution_session_id
        inbox.correlation_id = correlation_id

    async def _resolve_locked_active_binding(
        self,
        db: AsyncSession,
        *,
        workline: "WorkLine",
        candidate_binding: Any,
    ) -> Any:
        """复用仍与锁后 active pin 一致的候选 binding，否则重新解析。"""
        if candidate_binding is not None and getattr(candidate_binding, "id", None) == getattr(
            workline,
            "active_plugin_binding_id",
            None,
        ):
            return candidate_binding
        return await self.plugin_binding_service.resolve_new_session_binding(db, workline=workline)

    async def _reuse_locked_device_event_session(
        self,
        db: AsyncSession,
        inbox: Any,
        *,
        workline_id: int,
        candidate_business_key: str,
        active_business_key: str,
        trace: TraceContext,
        observed_at: Any,
    ) -> WorklineSession | None:
        """在候选键与 active plugin 键均已加锁后复查并复用既有 Session。"""
        if active_business_key != candidate_business_key:
            existing_session = await self._find_existing_device_event_session(
                db,
                inbox,
                workline_id=workline_id,
                business_key=candidate_business_key,
                trace=trace,
                observed_at=observed_at,
            )
            if existing_session is not None:
                await self._backfill_platform_execution_anchor(db, inbox=inbox, session=existing_session)
                return existing_session

        existing_session = await self._find_existing_device_event_session(
            db,
            inbox,
            workline_id=workline_id,
            business_key=active_business_key,
            trace=trace,
            observed_at=observed_at,
        )
        if existing_session is not None:
            await self._backfill_platform_execution_anchor(db, inbox=inbox, session=existing_session)
        return existing_session

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
        candidate_binding = None
        try:
            # 显式 business_key/barcode/event identity 足以命中既有 Session 时，
            # 不读取当前 binding；已有业务周期只沿自身已固定的 pin 执行。
            business_key = _resolve_business_key(payload_json, plugin_key=None)
        except SessionResolveError:
            candidate_binding = await self.plugin_binding_service.resolve_new_session_binding(db, workline=workline)
            candidate_plugin_key, candidate_contract_version = _binding_runtime_identity(
                candidate_binding,
                workline,
            )
            business_key = _resolve_business_key(
                payload_json,
                plugin_key=candidate_plugin_key,
                contract_version=candidate_contract_version,
            )
        now = timezone.now_for_db()
        trace = TraceContext.from_runtime(inbox=inbox, workline=workline)

        # 先无副作用探测历史业务周期；命中后只锁该历史键并在锁内复查，
        # 从而保留“不读取当前 binding”的历史 pin 快路径。
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

        # 只有确认需要创建新 Session 后才获取 WorkLine pin 共享锁。
        # 同一 WorkLine 的不同业务键可并行；activation/cutover 排他锁
        # 保证新 Session 不跨 binding 版本。
        await self.workline_repo.acquire_plugin_pin_shared(db, workline_id)
        current_workline = await self.workline_repo.get_current_plugin_pin(db, workline_id, populate_existing=True)
        if current_workline is None:
            raise ValueError(f"WorkLine not found: {workline_id}")
        workline = current_workline
        if not bool(getattr(workline, "is_active", False)):
            from src.app.workline.services.safety_service import WorkLineSafetyBlocked

            raise WorkLineSafetyBlocked(f"WorkLine 已停用，不再接收新工作: workline_id={workline_id}")
        active_binding = await self._resolve_locked_active_binding(
            db,
            workline=workline,
            candidate_binding=candidate_binding,
        )
        runtime_plugin_key, runtime_contract_version = _binding_runtime_identity(active_binding, workline)
        current_business_key = _resolve_business_key(
            payload_json,
            plugin_key=runtime_plugin_key,
            contract_version=runtime_contract_version,
        )
        trace = TraceContext.from_runtime(inbox=inbox, workline=workline)
        # 候选键与 active plugin 键必须在首次 advisory 前全部确定，并按稳定顺序获取。
        # 交叉 payload 因而不会形成 KEY-A→KEY-B / KEY-B→KEY-A 的循环等待。
        await _lock_device_event_business_keys(
            db,
            workline_id=workline_id,
            business_keys={business_key, current_business_key},
        )
        existing_session = await self._reuse_locked_device_event_session(
            db,
            inbox,
            workline_id=workline_id,
            candidate_business_key=business_key,
            active_business_key=current_business_key,
            trace=trace,
            observed_at=now,
        )
        if existing_session is not None:
            return existing_session
        business_key = current_business_key

        # 创建新 Session
        session_code = f"SES_{uuid.uuid4().hex[:16]}"

        trace_id = trace.trace_id or f"trace_{uuid.uuid4().hex}"
        inbox.trace_id = trace_id
        session_data: dict[str, Any] = {
            "session_code": session_code,
            "workline_id": workline_id,
            "plugin_key": runtime_plugin_key,
            "contract_version": active_binding.contract_version,
            "plugin_binding_id": active_binding.id,
            "plugin_binding_version": active_binding.binding_version,
            "plugin_config_hash": active_binding.typed_config_hash,
            "plugin_index_digest": active_binding.generated_index_digest,
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

        await self._pin_new_device_event_execution_anchor(
            db,
            inbox=inbox,
            workline=workline,
            session=new_session,
            binding=active_binding,
        )

        return new_session

    async def _backfill_platform_execution_anchor(
        self,
        db: AsyncSession,
        *,
        inbox: Any,
        session: WorklineSession,
    ) -> None:
        """复用平台 Session 时，仅恢复完整归属于该 Session 的 execution/correlation 锚点。"""

        if not isinstance(getattr(session, "plugin_binding_id", None), int):
            return
        session_code = non_empty_str(getattr(session, "session_code", None))
        session_trace_id = non_empty_str(getattr(session, "trace_id", None))
        business_key = non_empty_str(getattr(session, "business_key", None))
        plugin_key = non_empty_str(getattr(session, "plugin_key", None))
        contract_version = non_empty_str(getattr(session, "contract_version", None))
        plugin_config_hash = non_empty_str(getattr(session, "plugin_config_hash", None))
        plugin_index_digest = non_empty_str(getattr(session, "plugin_index_digest", None))
        plugin_binding_version = getattr(session, "plugin_binding_version", None)
        workline_id = getattr(session, "workline_id", None)
        if (
            session_code is None
            or session_trace_id is None
            or business_key is None
            or plugin_key is None
            or contract_version is None
            or plugin_config_hash is None
            or plugin_index_digest is None
            or not isinstance(plugin_binding_version, int)
            or not isinstance(workline_id, int)
        ):
            raise SessionResolveError("既有平台 Session 缺少完整 execution 归属身份")
        correlation_context = await self.execution_anchor_repo.resolve_owned_anchor(
            db,
            correlation_id=f"workline-session:{session_code}",
            trace_id=session_trace_id,
            workline_id=workline_id,
            plugin_key=plugin_key,
            contract_version=contract_version,
            plugin_binding_id=session.plugin_binding_id,
            plugin_binding_version=plugin_binding_version,
            plugin_config_hash=plugin_config_hash,
            plugin_index_digest=plugin_index_digest,
            business_key=business_key,
        )
        if correlation_context is None or not isinstance(correlation_context[1], int):
            raise SessionResolveError("既有平台 Session 缺少唯一 execution/correlation 锚点")
        correlation_id, execution_session_id = correlation_context
        current_execution_session_id = getattr(inbox, "execution_session_id", None)
        current_correlation_id = getattr(inbox, "correlation_id", None)
        if current_execution_session_id not in (None, execution_session_id) or current_correlation_id not in (
            None,
            correlation_id,
        ):
            raise SessionResolveError("Inbox execution anchor 与既有平台 Session 不一致")
        inbox.execution_session_id = execution_session_id
        inbox.correlation_id = correlation_id

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

        优先按 dispatch_key 找到 rack task，并通过 operation_key 找回等待中的物料 Session；
        找不到等待 Session 时回退 outbox/session_id 与 trace_id。

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
            session_by_rack_operation = await self._resolve_rack_task_material_session(db, inbox, dispatch_key)
            if session_by_rack_operation is not None:
                return session_by_rack_operation

            session_by_handling_operation = await self._resolve_handling_operation_material_session(
                db,
                inbox,
                dispatch_key,
            )
            if session_by_handling_operation is not None:
                return session_by_handling_operation

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

    async def _resolve_rack_task_material_session(
        self,
        db: AsyncSession,
        inbox: Any,
        dispatch_key: str,
    ) -> WorklineSession | None:
        """通过 rack task operation_key 找回被挂起的物料 session。"""

        rack_task = await self.rack_task_repo.get_by_dispatch_key(db, dispatch_key)
        if rack_task is None:
            logger.warning(
                "Rack task callback fallback to trace/outbox because rack task was not found: "
                f"dispatch_key={dispatch_key}"
            )
            return None

        workline_id = getattr(rack_task, "workline_id", None)
        if isinstance(workline_id, int):
            inbox.workline_id = workline_id
        else:
            logger.warning(
                "Rack task callback fallback to trace/outbox because workline_id is missing: "
                f"dispatch_key={dispatch_key}"
            )
            return None

        operation_key = non_empty_str(getattr(rack_task, "operation_key", None))
        if operation_key is None:
            logger.warning(
                "Rack task callback fallback to trace/outbox because operation_key is missing: "
                f"dispatch_key={dispatch_key}, workline_id={workline_id}"
            )
            return None

        session = await self.session_repo.get_open_session_by_waiting_rack_operation_key(
            db,
            workline_id=workline_id,
            operation_key=operation_key,
        )
        if session is None:
            logger.warning(
                "Rack task callback fallback to trace/outbox because no open session is waiting for operation: "
                f"dispatch_key={dispatch_key}, workline_id={workline_id}, operation_key={operation_key}"
            )
            return None

        inbox.workline_id = getattr(session, "workline_id", inbox.workline_id)
        return session

    async def _resolve_handling_operation_material_session(
        self,
        db: AsyncSession,
        inbox: Any,
        dispatch_key: str,
    ) -> WorklineSession | None:
        """通过 handling step operation_key 找回被挂起的物料 session。"""

        step = await self.handling_step_repo.get_by_dispatch_key(db, dispatch_key)
        if step is None:
            return None

        operation_key = non_empty_str(getattr(step, "operation_key", None))
        if operation_key is None:
            logger.warning(
                "Handling callback fallback to trace/outbox because operation_key is missing: "
                f"dispatch_key={dispatch_key}"
            )
            return None

        operation = await self.handling_operation_repo.get_by_operation_key(db, operation_key)
        workline_id = getattr(operation, "workline_id", None) if operation is not None else None
        if not isinstance(workline_id, int):
            logger.warning(
                "Handling callback fallback to trace/outbox because workline_id is missing: "
                f"dispatch_key={dispatch_key}, operation_key={operation_key}"
            )
            return None

        inbox.workline_id = workline_id
        session = await self.session_repo.get_open_session_by_waiting_handling_operation_key(
            db,
            workline_id=workline_id,
            operation_key=operation_key,
        )
        if session is None:
            logger.warning(
                "Handling callback fallback to trace/outbox because no open session is waiting for operation: "
                f"dispatch_key={dispatch_key}, workline_id={workline_id}, operation_key={operation_key}"
            )
            return None

        inbox.workline_id = getattr(session, "workline_id", inbox.workline_id)
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
