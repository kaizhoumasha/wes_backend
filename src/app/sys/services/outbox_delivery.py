from typing import Any
from uuid import uuid4

from src.app.sys.canonical_dispatch import ExternalHttpDispatchRequest
from src.app.sys.external_http_binding import FrozenExternalHttpBinding
from src.app.sys.external_http_credentials import CredentialResolutionError, VersionedCredentialProvider
from src.app.sys.external_http_transport import (
    ExternalHttpSender,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.core.logger import logger
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone

ALLOWED_INTERNAL_SIGNALS = frozenset({"core", "handling", "sys", "workline"})


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def dispatch_external_http(
    outbox: Any,
    credential_provider: VersionedCredentialProvider,
    http_sender: ExternalHttpSender,
) -> ExternalHttpTransportResult:
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
            credential_reference=getattr(outbox, "credential_reference", None),
        )
        secret = credential_provider.resolve(binding.credential_reference)
        request = ExternalHttpDispatchRequest.from_persisted(
            binding=binding,
            canonical_payload_bytes=getattr(outbox, "canonical_payload_bytes", None),
            payload_hash=getattr(outbox, "payload_hash", None),
            secret=secret,
            timestamp=timezone.now_utc().isoformat(),
            nonce=uuid4().hex,
        )
    except CredentialResolutionError as exc:
        logger.warning(f"EXTERNAL_HTTP credential preparation failed: {exc.code}")
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.PREPARING,
            safe_to_retry=False,
            error_code=exc.code,
            error_message="frozen credential is unavailable for dispatch",
        )
    except LookupError:
        logger.warning("EXTERNAL_HTTP credential preparation failed: CREDENTIAL_RESOLUTION_FAILED")
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.PREPARING,
            safe_to_retry=False,
            error_code="CREDENTIAL_RESOLUTION_FAILED",
            error_message="frozen credential could not be resolved",
        )
    except (TypeError, ValueError):
        logger.warning("EXTERNAL_HTTP frozen dispatch metadata is invalid")
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.PREPARING,
            safe_to_retry=False,
            error_code="DISPATCH_PREPARATION_FAILED",
            error_message="frozen target, binding, or canonical payload is invalid",
        )
    except Exception as exc:
        logger.error(f"EXTERNAL_HTTP credential provider raised {type(exc).__name__}")
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.PREPARING,
            safe_to_retry=False,
            error_code="CREDENTIAL_RESOLUTION_FAILED",
            error_message="frozen credential could not be resolved",
        )
    try:
        result = await http_sender(request)
    except Exception as exc:
        exception_name = type(exc).__name__
        logger.error(f"EXTERNAL_HTTP sender raised {exception_name}")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.SENDING,
            error_code="SENDER_RAISED",
            error_message=f"external HTTP sender raised {exception_name}",
        )
    if not isinstance(result, ExternalHttpTransportResult):
        logger.error("EXTERNAL_HTTP sender 违反 typed result 合同")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.SENDING,
            error_code="SENDER_CONTRACT_VIOLATION",
            error_message=f"unexpected sender result type: {type(result).__name__}",
        )
    return result


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
