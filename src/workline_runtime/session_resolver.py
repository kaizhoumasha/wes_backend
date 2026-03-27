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

if TYPE_CHECKING:
    from src.app.workline.models.inbox import WorklineInbox
    from src.app.workline.models.workline import WorkLine


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
        workline: "WorkLine",
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
            return await self._resolve_device_event(db, inbox, workline)
        if kind == InboxKind.COMMAND_RESULT:
            return await self._resolve_command_result(db, inbox)
        if kind == InboxKind.EXTERNAL_HTTP:
            return await self._resolve_external_http(db, inbox)
        if (
            kind == InboxKind.TIMER_TIMEOUT
            or kind in (InboxKind.MANUAL_HOLD, InboxKind.MANUAL_RESUME, InboxKind.MANUAL_CANCEL)
            or kind == InboxKind.REPLAY_REQUEST
        ):
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
        payload_json = inbox.payload_json if isinstance(inbox.payload_json, dict) else {}
        business_key = payload_json.get("business_key")

        # 如果没有 business_key，尝试从 data.barcode 提取
        if not isinstance(business_key, str) or not business_key:
            data = payload_json.get("data", {})
            if isinstance(data, dict):
                barcode = data.get("barcode")
                if isinstance(barcode, str) and barcode:
                    business_key = barcode

        # 如果仍然没有 business_key，生成一个唯一的
        if not isinstance(business_key, str) or not business_key:
            business_key = f"auto_{uuid.uuid4().hex[:12]}"

        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            raise TypeError("workline.id is required for DEVICE_EVENT")

        # 尝试查找已存在的 Session
        existing_session = await self.session_repo.get_open_session_by_business_key(
            db=db,
            workline_id=workline_id,
            business_key=business_key,
        )

        if existing_session:
            return existing_session

        # 创建新 Session
        session_code = f"SES_{uuid.uuid4().hex[:16]}"
        now = timezone.now_for_db()

        session_data: dict[str, Any] = {
            "session_code": session_code,
            "workline_id": workline_id,
            "plugin_key": getattr(workline, "plugin_key", None) or getattr(workline, "line_code", "default"),
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
        payload_json = inbox.payload_json if isinstance(inbox.payload_json, dict) else {}
        command_code = payload_json.get("command_code")
        if not isinstance(command_code, str) or not command_code:
            raise ValueError("command_code is required for COMMAND_RESULT")

        command = await self.command_repo.get_by_command_code(db, command_code)
        if command is None:
            raise ValueError(f"DeviceCommand not found for command_code: {command_code}")

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
