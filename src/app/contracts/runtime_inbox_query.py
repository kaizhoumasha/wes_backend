"""RuntimeInbox 跨域只读 query contract。"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003 - runtime type introspection contract
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


@dataclass(frozen=True, slots=True)
class RuntimeInboxProjection:
    """查询、trace 与 evidence 使用的冻结外壳；payload_json 是深拷贝快照。"""

    id: int
    kind: str | None
    provider_code: str
    event_type: str
    source_event_id: str | None
    payload_json: Mapping[str, Any]
    payload_hash: str | None
    payload_schema_version: int | None
    workline_session_ref: int | None
    execution_session_id: int | None
    workline_id: int | None
    device_id: int | None
    command_id: int | None
    correlation_id: str | None
    trace_id: str | None
    event_id: str | None
    causation_id: str | None
    status: str
    attempt_count: int
    max_retries: int
    next_retry_at: int | None
    received_at: int | None
    processed_at: int | None
    failed_at: int | None
    last_error_code: str | None
    last_error_message: str | None


class RuntimeInboxQueryPort(Protocol):
    """业务 repository 可依赖的 RuntimeInbox 只读端口。"""

    async def get_evidence_by_id(self, db: Any, inbox_id: int) -> RuntimeInboxEvidence | None: ...

    async def count_unfinished_by_workline(self, db: Any, workline_id: int) -> int: ...

    async def first_unfinished_by_workline(self, db: Any, workline_id: int) -> RuntimeInboxWorkloadSample | None: ...

    async def count_by_statuses(self, db: Any, statuses: set[str]) -> int: ...

    async def latest_by_workline_session_refs(
        self, db: Any, *, workline_session_refs: list[int], kind: str | None = None
    ) -> dict[int, RuntimeInboxProjection]: ...

    async def list_by_trace_id(self, db: Any, trace_id: str) -> list[RuntimeInboxProjection]: ...

    async def list_by_workline_session_ref(
        self, db: Any, workline_session_ref: int
    ) -> list[RuntimeInboxProjection]: ...

    def workline_session_ref_exists_for_device(self, device_id: int, outer_ref_column: Any) -> Any: ...


__all__ = [
    "RUNTIME_INBOX_UNFINISHED_STATUSES",
    "RuntimeInboxEvidence",
    "RuntimeInboxProjection",
    "RuntimeInboxQueryPort",
    "RuntimeInboxWorkloadSample",
]
