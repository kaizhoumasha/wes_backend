"""RuntimeInbox 通用终态写回。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxService,
    runtime_inbox_service,
)
from src.app.workline.utils import payload_dict


class RuntimeInboxLeaseLostError(RuntimeError):
    """RuntimeInbox 的 lease fencing 未命中。"""


@dataclass(frozen=True, slots=True)
class RuntimeInboxWriteBackResult:
    """通用终态写回结果。"""

    processed: bool


def _require_fenced_update(updated: bool, *, action: str, inbox_id: int) -> None:
    if not updated:
        raise RuntimeInboxLeaseLostError(f"RuntimeInbox {inbox_id} lease lost before {action}")


def _payload_for_inbox(inbox: Any) -> dict[str, Any]:
    return payload_dict(getattr(inbox, "payload_json", None))


class RuntimeInboxWriteBackService:
    """仅负责 RuntimeInbox 通用终态的 fenced 写回。"""

    def __init__(self, *, inbox_service: RuntimeInboxService | None = None) -> None:
        self._inbox_service = inbox_service or runtime_inbox_service

    async def mark_processed(self, db: Any, *, inbox_id: int, lease_token: str) -> RuntimeInboxWriteBackResult:
        _require_fenced_update(
            await self._inbox_service.mark_processed(db, inbox_id=inbox_id, lease_token=lease_token),
            action="mark_processed",
            inbox_id=inbox_id,
        )
        return RuntimeInboxWriteBackResult(processed=True)


__all__ = [
    "RuntimeInboxLeaseLostError",
    "RuntimeInboxWriteBackResult",
    "RuntimeInboxWriteBackService",
]
