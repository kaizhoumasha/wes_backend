"""EVENT_DEBUG 命令创建的内部结果。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.device.models.command import CommandStatus


@dataclass(frozen=True, slots=True)
class EventDebugCommandReady:
    """命令已可用；只有本次新建才需要唤醒派发。"""

    command_code: str
    status: CommandStatus
    created: bool


__all__ = ["EventDebugCommandReady"]
