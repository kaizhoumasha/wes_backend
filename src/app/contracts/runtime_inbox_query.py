"""RuntimeInbox 跨域只读 query contract。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

RUNTIME_INBOX_UNFINISHED_STATUSES = ("RECEIVED", "PROCESSING", "FAILED")


@dataclass(frozen=True, slots=True)
class RuntimeInboxEvidence:
    """供业务域展示的 RuntimeInbox 不可变证据，避免泄漏入站 ORM。"""

    id: int
    status: str
    event_id: str | None
    attempt_count: int
    max_retries: int
    next_retry_at: int | None
    processed_at: int | None
    failed_at: int | None
    last_error_code: str | None
    last_error_message: str | None


@dataclass(frozen=True, slots=True)
class RuntimeInboxWorkloadSample:
    """WorkLine 未完成负载中的首条 RuntimeInbox 证据。"""

    id: int
    status: str


class RuntimeInboxQueryPort(Protocol):
    """业务 repository 可依赖的 RuntimeInbox 只读端口。"""

    async def get_evidence_by_id(self, db: Any, inbox_id: int) -> RuntimeInboxEvidence | None: ...

    async def count_unfinished_by_workline(self, db: Any, workline_id: int) -> int: ...

    async def first_unfinished_by_workline(self, db: Any, workline_id: int) -> RuntimeInboxWorkloadSample | None: ...


__all__ = [
    "RUNTIME_INBOX_UNFINISHED_STATUSES",
    "RuntimeInboxEvidence",
    "RuntimeInboxQueryPort",
    "RuntimeInboxWorkloadSample",
]
