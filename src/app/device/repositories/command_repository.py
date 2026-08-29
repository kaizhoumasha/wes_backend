"""DeviceCommand 最终持久化 owner。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, cast

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.workline.models.line_run_epoch import LineRunEpoch
from src.database.base_repository import BaseRepository

_UNCLOSED_STATUSES = (
    CommandStatus.PENDING,
    CommandStatus.DISPATCHING,
    CommandStatus.ACKNOWLEDGED,
    CommandStatus.RECONCILING,
)


class DeviceCommandRepository(BaseRepository[DeviceCommand]):
    """命令创建、领取和 fenced 写回查询。"""

    def __init__(self) -> None:
        super().__init__(DeviceCommand)

    async def lock_creation_for_device(self, db: AsyncSession, device_code: str) -> None:
        """串行化同一设备的命令创建，覆盖尚无可锁记录的首次创建。"""

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_identity, 0))"),
            {"lock_identity": f"device-command:create:{device_code}"},
        )

    async def lock_manual_debug_identity(self, db: AsyncSession, client_request_id: str) -> None:
        """串行化 MANUAL_DEBUG 幂等身份，覆盖跨设备的首次创建。"""

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_identity, 0))"),
            {"lock_identity": f"device-command:manual-debug:{client_request_id}"},
        )

    async def get_by_command_code(
        self,
        db: AsyncSession,
        command_code: str,
        *,
        for_update: bool = False,
    ) -> DeviceCommand | None:
        columns = cast("Any", DeviceCommand).__table__.c
        statement = select(DeviceCommand).where(columns.command_code == command_code)
        if for_update:
            statement = statement.with_for_update()
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def get_unclosed_for_device_for_update(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> DeviceCommand | None:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(columns.device_code == device_code, columns.status.in_(_UNCLOSED_STATUSES))
            .order_by(columns.id)
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def has_unclosed_for_epoch_for_update(self, db: AsyncSession, line_run_epoch_id: int) -> bool:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(columns.id)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.status.in_(_UNCLOSED_STATUSES),
            )
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none() is not None

    async def get_by_execution_ref_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int | None,
        device_code: str,
        execution_ref_type: str,
        execution_ref_id: str,
    ) -> DeviceCommand | None:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.device_code == device_code,
                columns.execution_ref_type == execution_ref_type,
                columns.execution_ref_id == execution_ref_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_manual_debug_by_client_request_id_for_update(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> DeviceCommand | None:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                columns.execution_ref_type == "MANUAL_DEBUG",
                columns.execution_ref_id == client_request_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add(self, db: AsyncSession, command: DeviceCommand) -> DeviceCommand:
        db.add(command)
        await db.flush()
        return command

    async def list_for_material_execution(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
        material_execution_id: int,
    ) -> list[DeviceCommand]:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                columns.line_run_epoch_id == line_run_epoch_id,
                columns.material_execution_id == material_execution_id,
            )
            .order_by(columns.created_at, columns.id)
        )
        return list(result.scalars())

    async def list_for_epoch_for_update(
        self,
        db: AsyncSession,
        *,
        line_run_epoch_id: int,
    ) -> list[DeviceCommand]:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(columns.line_run_epoch_id == line_run_epoch_id)
            .order_by(columns.created_at, columns.id)
            .with_for_update()
        )
        return list(result.scalars())

    async def claim_next_pending(
        self,
        db: AsyncSession,
        *,
        token: str,
        now: datetime,
        claim_expires_at: datetime,
    ) -> DeviceCommand | None:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                columns.status == CommandStatus.PENDING,
                columns.deadline_at > now,
                (columns.next_attempt_at.is_(None) | (columns.next_attempt_at <= now)),
            )
            .order_by(columns.next_attempt_at.asc().nullsfirst(), columns.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        command = result.scalar_one_or_none()
        if command is None:
            return None
        command.transition_to(CommandStatus.DISPATCHING)
        command.claim_token = token
        command.claimed_at = now
        command.claim_expires_at = claim_expires_at
        command.attempt_count += 1
        await db.flush()
        return command

    async def get_claimed_for_update(
        self,
        db: AsyncSession,
        *,
        command_code: str,
        claim_token: str,
    ) -> DeviceCommand | None:
        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                columns.command_code == command_code,
                columns.claim_token == claim_token,
                columns.status == CommandStatus.DISPATCHING,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def claim_next_reconcilable(
        self,
        db: AsyncSession,
        *,
        now: datetime,
    ) -> DeviceCommand | None:
        """锁定一条已到期命令；状态解释由应用服务完成。"""

        columns = cast("Any", DeviceCommand).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .where(
                or_(
                    (columns.status == CommandStatus.PENDING) & (columns.deadline_at <= now),
                    (columns.status == CommandStatus.DISPATCHING) & (columns.claim_expires_at <= now),
                    (columns.status == CommandStatus.ACKNOWLEDGED) & (columns.deadline_at <= now),
                )
            )
            .order_by(columns.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def release_retryable(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        next_attempt_at: datetime,
    ) -> None:
        command.transition_to(CommandStatus.PENDING)
        command.next_attempt_at = next_attempt_at
        _clear_claim(command)
        await db.flush()

    async def mark_acknowledged(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        acknowledged_at: datetime,
    ) -> None:
        command.transition_to(CommandStatus.ACKNOWLEDGED)
        command.ack_received_at = acknowledged_at
        _clear_claim(command)
        await db.flush()

    async def mark_failed(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        failure_code: str,
    ) -> None:
        command.failure_code = failure_code
        command.transition_to(CommandStatus.FAILED)
        _clear_claim(command)
        await db.flush()

    async def mark_timed_out(self, db: AsyncSession, command: DeviceCommand) -> None:
        command.transition_to(CommandStatus.TIMED_OUT)
        _clear_claim(command)
        await db.flush()

    async def mark_reconciling(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        reason: str,
    ) -> None:
        command.reconciliation_reason = reason
        command.transition_to(CommandStatus.RECONCILING)
        _clear_claim(command)
        await db.flush()

    async def mark_late_ack_reconciling(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        acknowledged_at: datetime,
    ) -> None:
        command.ack_received_at = acknowledged_at
        command.reconciliation_reason = "ACK_AFTER_DEADLINE"
        command.transition_to(CommandStatus.RECONCILING)
        _clear_claim(command)
        await db.flush()

    async def fail_pending_by_workline(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        failure_code: str,
        limit: int = 100,
    ) -> int:
        """急停只关闭尚未发送的命令；已可能触发物理动作的命令继续占槽。"""

        command_columns = cast("Any", DeviceCommand).__table__.c
        epoch_columns = cast("Any", LineRunEpoch).__table__.c
        result = await db.execute(
            select(DeviceCommand)
            .join(LineRunEpoch, epoch_columns.id == command_columns.line_run_epoch_id)
            .where(
                epoch_columns.workline_id == workline_id,
                command_columns.status == CommandStatus.PENDING,
            )
            .order_by(command_columns.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        commands = list(result.scalars().all())
        for command in commands:
            command.failure_code = failure_code
            command.transition_to(CommandStatus.FAILED)
        await db.flush()
        return len(commands)


device_command_repository = DeviceCommandRepository()


def _clear_claim(command: DeviceCommand) -> None:
    command.claim_token = None
    command.claimed_at = None
    command.claim_expires_at = None


__all__ = ["DeviceCommandRepository", "device_command_repository"]
