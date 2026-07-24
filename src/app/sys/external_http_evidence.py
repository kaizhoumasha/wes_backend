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


def is_late_external_http_result_target(outbox: Any) -> bool:
    """识别已跨越物理发送边界、随后被 fence 为 UNKNOWN 的 outbox。"""

    from src.utils.value_normalization import enum_value

    return (
        enum_value(getattr(outbox, "status", None)) == "UNKNOWN"
        and getattr(outbox, "dispatch_started_at", None) is not None
    )


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
    lease_owner_token: str,
    result: ExternalHttpTransportResult,
    cause: BaseException,
    recovery_context_factory: Any,
    attempt_service: Any,
    effect_transport_bridge: Any,
    dispatch_key: str,
    attempt_no: int,
) -> Any:
    """回滚原事务，并用独立短事务把 outbox、attempt 与 intent 收口为 UNKNOWN。"""

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
            from src.utils.value_normalization import enum_value

            current = await outbox_repository.get_by_id_for_update(recovery_db, outbox_id)
            callback_completed = (
                enum_value(getattr(current, "status", None)) == "SENT"
                and getattr(current, "finished_at", None) is not None
            )
            late_result_target = is_late_external_http_result_target(current)
            if callback_completed:
                updated = current
                recovery_result = result
                outbox_finalization = "sent"
            elif late_result_target:
                # cancellation/lease-loss 已封存三本账；恢复事务只补原始 transport
                # evidence，不能再次改写 UNKNOWN outbox 或终结 UNKNOWN attempt。
                updated = current
                recovery_result = result
                outbox_finalization = "unknown"
            else:
                from src.app.sys.external_http_transport import (
                    ExternalHttpProtocolResult,
                    ExternalHttpTransportPhase,
                    ExternalHttpTransportResult,
                )

                recovery_result = ExternalHttpTransportResult.ambiguous(
                    phase=result.phase,
                    protocol_result=(
                        ExternalHttpProtocolResult.UNKNOWN
                        if result.phase is ExternalHttpTransportPhase.RESPONSE_RECEIVED
                        else ExternalHttpProtocolResult.NOT_AVAILABLE
                    ),
                    http_status_code=(
                        result.http_status_code
                        if result.phase is ExternalHttpTransportPhase.RESPONSE_RECEIVED
                        else None
                    ),
                    error_code=EVIDENCE_PERSISTENCE_FAILURE_CODE,
                    error_message=evidence_error,
                )
                updated = await outbox_repository.mark_evidence_persistence_unknown(
                    recovery_db,
                    outbox_id,
                    evidence_error,
                    lease_owner_token=lease_owner_token,
                )
                if updated is None:
                    raise RuntimeError(f"SystemOutbox {outbox_id} 无法隔离收口为 UNKNOWN")
                outbox_finalization = "unknown"
            if not late_result_target:
                await attempt_service.finalize_external_http_attempt(
                    recovery_db,
                    lease_token=lease_owner_token,
                    result=recovery_result,
                    outbox_finalization=outbox_finalization,
                    auto_commit=False,
                )
            from src.utils.timezone import timezone

            await effect_transport_bridge.record_result(
                recovery_db,
                dispatch_key=dispatch_key,
                attempt_no=attempt_no,
                result=recovery_result,
                retry_exhausted=False,
                occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
            )
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
    "is_late_external_http_result_target",
    "recover_external_http_evidence_failure_unknown",
]
