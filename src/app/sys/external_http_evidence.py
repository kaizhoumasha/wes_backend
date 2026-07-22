"""EXTERNAL_HTTP 派发证据持久化失败时的保守恢复。"""

from __future__ import annotations

from inspect import isawaitable
from typing import TYPE_CHECKING, Any

from src.core.logger import logger

if TYPE_CHECKING:
    from src.app.sys.external_http_transport import ExternalHttpTransportResult

EVIDENCE_PERSISTENCE_FAILURE_CODE = "EXTERNAL_HTTP_EVIDENCE_PERSISTENCE_FAILED"


class ExternalHttpEvidenceRecoveryError(RuntimeError):
    """证据落库失败后，隔离 UNKNOWN 恢复也失败。"""


def build_external_http_evidence_failure_error(
    result: ExternalHttpTransportResult,
    cause: BaseException,
) -> str:
    """生成不含请求正文的最小恢复证据。"""

    cause_message = " ".join(str(cause).split())[:500]
    return (
        f"{EVIDENCE_PERSISTENCE_FAILURE_CODE} "
        f"outcome={result.outcome.value} phase={result.phase.value} "
        f"protocol={result.protocol_result.value} "
        f"cause={type(cause).__name__}:{cause_message}"
    )


async def recover_external_http_evidence_failure_unknown(
    active_db: Any,
    *,
    outbox_repository: Any,
    outbox_id: int,
    result: ExternalHttpTransportResult,
    cause: BaseException,
    recovery_context_factory: Any,
) -> Any:
    """回滚原事务，并用独立短事务把外部副作用收口为 UNKNOWN。"""

    rollback = getattr(active_db, "rollback", None)
    if callable(rollback):
        try:
            rollback_result = rollback()
            if isawaitable(rollback_result):
                await rollback_result
        except Exception as rollback_error:  # pragma: no cover - 独立恢复仍可继续
            logger.warning(f"EXTERNAL_HTTP 证据失败原事务回滚异常: outbox_id={outbox_id}, error={rollback_error}")

    evidence_error = build_external_http_evidence_failure_error(result, cause)
    try:
        async with recovery_context_factory() as recovery_db:
            updated = await outbox_repository.mark_evidence_persistence_unknown(
                recovery_db,
                outbox_id,
                evidence_error,
            )
            if updated is None:
                raise RuntimeError(f"SystemOutbox {outbox_id} 无法隔离收口为 UNKNOWN")
            commit = getattr(recovery_db, "commit", None)
            if not callable(commit):
                raise TypeError("隔离恢复数据库会话缺少 commit")
            commit_result = commit()
            if isawaitable(commit_result):
                await commit_result
            return updated
    except Exception as recovery_error:
        raise ExternalHttpEvidenceRecoveryError(
            f"EXTERNAL_HTTP 证据失败且 UNKNOWN 隔离恢复失败: outbox_id={outbox_id}"
        ) from recovery_error


__all__ = [
    "EVIDENCE_PERSISTENCE_FAILURE_CODE",
    "ExternalHttpEvidenceRecoveryError",
    "build_external_http_evidence_failure_error",
    "recover_external_http_evidence_failure_unknown",
]
