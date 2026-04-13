"""
Session 归属解析器

根据 Inbox 类型和归属规则解析或创建 Session。

规则（按 InboxKind）:
- DEVICE_EVENT: 按 device_id + business_key 查找或创建
- COMMAND_RESULT: 按 command_code -> awaiting_command_id / correlation_id 恢复 Session
- EXTERNAL_HTTP: 按 correlation_id 恢复 Session
- TIMER_TIMEOUT: 按 session_id 恢复 Session
- MANUAL_*: 按 session_id 恢复 Session

设计参考: 设计文档 phase2-orchestrator
"""

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.repositories.command_repository import DeviceCommandRepository
from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.repositories.session_repository import (
    WorklineSessionRepository,
    workline_session_repository,
)
from src.utils.timezone import timezone
from src.workline_plugin_registry import get_plugin_contract_version
from src.workline_runtime.utils import ensure_dict

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
_SIX_IN_ONE_KEY_ALIASES = ("LotCode", "DateCode", "ProductNo", "MfrPN", "PONumber", "Qty")


def _generate_six_in_one_business_key(data: dict[str, Any]) -> str | None:
    """
    从 Six-In-One 数据生成唯一业务键。

    使用所有非空字段生成 hash，确保相同数据组合生成相同 key，
    不同组合生成不同 key。

    Args:
        data: 业务数据字典

    Returns:
        16位唯一业务键，失败返回 None
    """
    # 收集所有非空字段，保持固定顺序
    fields = []
    for alias in _SIX_IN_ONE_KEY_ALIASES:
        value = data.get(alias)
        if isinstance(value, str) and value:
            fields.append(value)

    if not fields:
        return None

    # 生成确定性的 JSON 字符串
    json_str = json.dumps(fields, ensure_ascii=False)

    # 生成 16 位 hash
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:16]


def _resolve_business_key(payload_json: dict[str, Any]) -> str:
    """从事件 payload 提取业务主键，缺失时生成兜底值。"""
    business_key = payload_json.get("business_key")
    if isinstance(business_key, str) and business_key:
        return business_key

    data = ensure_dict(payload_json.get("data"))

    # 优先使用 barcode 字段
    barcode = data.get("barcode")
    if isinstance(barcode, str) and barcode:
        return barcode

    # 使用完整的 Six-In-One 组合生成唯一业务键
    six_in_one_key = _generate_six_in_one_business_key(data)
    if six_in_one_key:
        return six_in_one_key

    return f"auto_{uuid.uuid4().hex[:12]}"


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
        business_key = _resolve_business_key(payload_json)

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
            return existing_session

        # 2. 如果没有未结束的 Session，尝试通过 correlation_id 查找
        correlation_id = getattr(inbox, "correlation_id", None)
        if correlation_id:
            session_by_corr = await self.session_repo.get_by_correlation_id(
                db=db,
                correlation_id=correlation_id,
            )
            if session_by_corr:
                inbox.session_id = session_by_corr.id
                return session_by_corr

        # 3. 如果还是没有，查找最新的 Session（处理事件在 session 完成后立即到达的情况）
        latest_session = await self.session_repo.get_latest_session_by_business_key(
            db=db,
            workline_id=workline_id,
            business_key=business_key,
        )

        if latest_session:
            # 如果最新的 session 刚完成不久（5秒内），继续使用它
            now = timezone.now_for_db()
            if latest_session.ended_at:
                elapsed = (now - latest_session.ended_at).total_seconds()
                if elapsed < 5:
                    inbox.session_id = latest_session.id
                    inbox.correlation_id = latest_session.correlation_id
                    return latest_session

        # 创建新 Session
        session_code = f"SES_{uuid.uuid4().hex[:16]}"
        now = timezone.now_for_db()

        session_data: dict[str, Any] = {
            "session_code": session_code,
            "workline_id": workline_id,
            "plugin_key": getattr(workline, "plugin_key", None),
            "business_key": business_key,
            "status": SessionStatus.NEW,
            "correlation_id": getattr(inbox, "correlation_id", None) or f"corr_{uuid.uuid4().hex}",
            "context_json": {
                "device_id": inbox.device_id,
                "source_message_id": getattr(inbox, "source_message_id", None),
                "initial_payload": payload_json,
            },
            "started_at": now,
        }

        contract_version = get_plugin_contract_version(getattr(workline, "plugin_key", None))
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

        inbox.command_id = command.id
        inbox.device_id = command.device_id
        if command.workline_id is not None:
            inbox.workline_id = command.workline_id
        if command.correlation_id:
            inbox.correlation_id = command.correlation_id

        session = await self.session_repo.get_open_session_by_awaiting_command_id(db, command.id)
        if session:
            inbox.session_id = session.id
            return session

        if command.correlation_id:
            session = await self.session_repo.get_by_correlation_id(db, command.correlation_id)
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

        按 correlation_id 恢复 Session。

        Args:
            db: 数据库会话
            inbox: 收件箱消息

        Returns:
            解析的 Session

        Raises:
            ValueError: 当 correlation_id 缺失或 Session 不存在时
        """
        correlation_id = inbox.correlation_id

        if not correlation_id:
            raise ValueError("correlation_id is required for EXTERNAL_HTTP")

        # 按 correlation_id 查找 Session
        session = await self.session_repo.get_by_correlation_id(
            db=db,
            correlation_id=correlation_id,
        )

        if not session:
            raise ValueError(f"Session not found for correlation_id: {correlation_id}")

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
