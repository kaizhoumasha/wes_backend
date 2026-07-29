import time
from typing import Any
from uuid import uuid4

from src.app.sys.canonical_dispatch import ExternalHttpDispatchRequest
from src.app.sys.external_http_binding import FrozenExternalHttpBinding
from src.app.sys.external_http_credentials import CredentialResolutionError, VersionedCredentialProvider
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpSender,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.core.logger import logger
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone

ALLOWED_INTERNAL_SIGNALS = frozenset({"core", "handling", "sys", "workline"})


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _observed_external_http_result(
    outbox: Any,
    result: ExternalHttpTransportResult,
    *,
    started_at: float,
) -> ExternalHttpTransportResult:
    """best-effort 发射脱敏 attempt 观测，保持 transport typed result 不变。"""

    from src.app.runtime.orchestration.operation_observability import emit_northbound_operation_observation

    if result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS:
        outcome = "UNKNOWN"
    elif result.outcome is ExternalHttpTransportOutcome.NOT_SENT:
        outcome = "TECHNICAL_FAILURE"
    elif result.protocol_result is ExternalHttpProtocolResult.REJECTED:
        outcome = "BUSINESS_REJECT"
    else:
        outcome = "SUCCESS"
    outbox_id = getattr(outbox, "id", None)
    trace_id = getattr(outbox, "trace_id", None) or f"outbox:{outbox_id or 'UNAVAILABLE'}"
    correlation_id = getattr(outbox, "dispatch_key", None) or trace_id
    evidence_ref = f"outbox-attempt:{outbox_id or 'UNAVAILABLE'}:{getattr(outbox, 'attempt_count', 0)}"
    try:
        _ = emit_northbound_operation_observation(
            operation_identity=str(getattr(outbox, "operation_identity", "")),
            provider_profile_identity=str(getattr(outbox, "provider_profile_identity", "")),
            outcome=outcome,
            latency_ms=(time.perf_counter() - started_at) * 1_000,
            trace_id=str(trace_id),
            correlation_id=str(correlation_id),
            evidence_ref=evidence_ref,
            stage="DISPATCH_ATTEMPT",
        )
    except Exception as exc:  # pragma: no cover - 观测失败不改变 transport 结果
        logger.warning(f"EXTERNAL_HTTP observability emission failed: {type(exc).__name__}")
    return result


async def dispatch_external_http(
    outbox: Any,
    credential_provider: VersionedCredentialProvider,
    http_sender: ExternalHttpSender,
) -> ExternalHttpTransportResult:
    started_at = time.perf_counter()
    try:
        binding = FrozenExternalHttpBinding.from_persisted(
            provider_profile_identity=getattr(outbox, "provider_profile_identity", None),
            provider_profile_hash=getattr(outbox, "provider_profile_hash", None),
            operation_identity=getattr(outbox, "operation_identity", None),
            binding_revision=getattr(outbox, "binding_revision", None),
            target_code=getattr(outbox, "target_code", None),
            target_snapshot_json=getattr(outbox, "target_snapshot_json", None),
            target_snapshot_hash=getattr(outbox, "target_snapshot_hash", None),
            auth_scheme=getattr(outbox, "auth_scheme", None),
            network_trust_mode=getattr(outbox, "network_trust_mode", None),
            credential_reference=getattr(outbox, "credential_reference", None),
        )
        secret = None
        timestamp = None
        nonce = None
        if binding.auth_scheme == "HMAC_SHA256":
            credential_reference = binding.credential_reference
            if credential_reference is None:
                raise ValueError("HMAC_SHA256 frozen binding requires credential reference")
            secret = credential_provider.resolve(credential_reference)
            timestamp = timezone.now_utc().isoformat()
            nonce = uuid4().hex
        request = ExternalHttpDispatchRequest.from_persisted(
            binding=binding,
            canonical_payload_bytes=getattr(outbox, "canonical_payload_bytes", None),
            payload_hash=getattr(outbox, "payload_hash", None),
            idempotency_key=getattr(outbox, "idempotency_key", None),
            secret=secret,
            timestamp=timestamp,
            nonce=nonce,
        )
    except CredentialResolutionError as exc:
        logger.warning(f"EXTERNAL_HTTP credential preparation failed: {exc.code}")
        return _observed_external_http_result(
            outbox,
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.PREPARING,
                safe_to_retry=False,
                error_code=exc.code,
                error_message="frozen credential is unavailable for dispatch",
            ),
            started_at=started_at,
        )
    except LookupError:
        logger.warning("EXTERNAL_HTTP credential preparation failed: CREDENTIAL_RESOLUTION_FAILED")
        return _observed_external_http_result(
            outbox,
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.PREPARING,
                safe_to_retry=False,
                error_code="CREDENTIAL_RESOLUTION_FAILED",
                error_message="frozen credential could not be resolved",
            ),
            started_at=started_at,
        )
    except (TypeError, ValueError):
        logger.warning("EXTERNAL_HTTP frozen dispatch metadata is invalid")
        return _observed_external_http_result(
            outbox,
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.PREPARING,
                safe_to_retry=False,
                error_code="DISPATCH_PREPARATION_FAILED",
                error_message="frozen target, binding, or canonical payload is invalid",
            ),
            started_at=started_at,
        )
    except Exception as exc:
        logger.error(f"EXTERNAL_HTTP credential provider raised {type(exc).__name__}")
        return _observed_external_http_result(
            outbox,
            ExternalHttpTransportResult.not_sent(
                phase=ExternalHttpTransportPhase.PREPARING,
                safe_to_retry=False,
                error_code="CREDENTIAL_RESOLUTION_FAILED",
                error_message="frozen credential could not be resolved",
            ),
            started_at=started_at,
        )
    try:
        result = await http_sender(request)
    except Exception as exc:
        exception_name = type(exc).__name__
        logger.error(f"EXTERNAL_HTTP sender raised {exception_name}")
        return _observed_external_http_result(
            outbox,
            ExternalHttpTransportResult.ambiguous(
                phase=ExternalHttpTransportPhase.SENDING,
                error_code="SENDER_RAISED",
                error_message=f"external HTTP sender raised {exception_name}",
            ),
            started_at=started_at,
        )
    if not isinstance(result, ExternalHttpTransportResult):
        logger.error("EXTERNAL_HTTP sender 违反 typed result 合同")
        return _observed_external_http_result(
            outbox,
            ExternalHttpTransportResult.ambiguous(
                phase=ExternalHttpTransportPhase.SENDING,
                error_code="SENDER_CONTRACT_VIOLATION",
                error_message=f"unexpected sender result type: {type(result).__name__}",
            ),
            started_at=started_at,
        )
    return _observed_external_http_result(outbox, result, started_at=started_at)


async def dispatch_internal_signal(outbox: Any, queue_gateway: TaskQueueGateway = task_queue_gateway) -> bool:
    target_code = getattr(outbox, "target_code", None)
    if not isinstance(target_code, str) or target_code not in ALLOWED_INTERNAL_SIGNALS:
        logger.error(f"SystemOutbox 内部信号派发失败: 未知的目标服务 {target_code}")
        return False

    try:
        queue_gateway.enqueue_internal_signal(target_code, _payload_dict(getattr(outbox, "payload_json", None)))
        return True
    except Exception as exc:
        logger.error(f"SystemOutbox 内部信号派发失败: {exc}")
        return False
