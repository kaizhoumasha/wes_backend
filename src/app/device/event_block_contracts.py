"""EVENT_DEBUG 命令创建的内部因果结果。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.device.models.command import CommandStatus
    from src.app.device.models.event_command_block import DeviceEventCommandBlockStatus
    from src.app.execution.models.inbound_evidence import InboundEvidenceApplyStatus


@dataclass(frozen=True, slots=True)
class EventDebugCommandReady:
    """命令已可用；只有本次新建才需要唤醒派发。"""

    command_code: str
    status: CommandStatus
    created: bool


@dataclass(frozen=True, slots=True)
class EventDebugCommandBlocked:
    """旧命令占用设备执行槽的检测时快照。"""

    blocking_command_id: int
    blocking_command_code: str
    blocking_command_status: CommandStatus
    blocking_reconciliation_reason: str | None


@dataclass(frozen=True, slots=True)
class EventCommandBlockSnapshot:
    """供持久 blocker 查询 API 使用的窄快照。"""

    block_id: int
    status: DeviceEventCommandBlockStatus
    source_event_id: str
    device_code: str
    blocking_command_code: str
    blocking_command_detected_status: CommandStatus
    blocking_command_detected_reconciliation_reason: str | None
    blocking_command_current_status: CommandStatus | None
    blocking_command_terminal: bool
    reason_code: str
    blocked_at: datetime
    requeued_at: datetime | None
    reconcile_device_idle_path: str
    reprocess_path: str


@dataclass(frozen=True, slots=True)
class ReprocessedEventSnapshot:
    """显式重处理只承诺 evidence 已重新进入 PENDING。"""

    source_event_id: str
    block_id: int
    apply_status: InboundEvidenceApplyStatus


__all__ = [
    "EventCommandBlockSnapshot",
    "EventDebugCommandBlocked",
    "EventDebugCommandReady",
    "ReprocessedEventSnapshot",
]
