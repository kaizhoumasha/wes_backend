"""Registry QUERY 对现有 evidence 主账与 breaker 的统一适配。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from src.app.wms_integration.models import WmsEvidenceStatus
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QuerySuccess,
    QueryTechnicalFailure,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.wms_integration.services.circuit_breaker_service import WmsCircuitBreakerService
    from src.app.wms_integration.services.evidence_service import WmsCallEvidenceService


@dataclass(frozen=True, slots=True)
class WmsQueryCallPermit:
    """QUERY 与 EFFECT status 共用的 breaker 准入凭据。"""

    allowed: bool
    reason: str | None = None
    retry_after_seconds: float | None = None
    probe_generation: int | None = None


class WmsEffectStatusEvidenceWriter(Protocol):
    """EFFECT status 查询使用的 evidence/breaker 边界。"""

    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit: ...

    async def record(
        self,
        *,
        operation_identity: str,
        target_code: str,
        request_snapshot: Mapping[str, object],
        outcome: object,
        permit: WmsQueryCallPermit,
    ) -> str: ...


class WmsEffectStatusCallEvidenceWriter:
    """以独立短事务把 EFFECT status outcome 写入现有 evidence 主账。"""

    def __init__(
        self,
        *,
        session_factory,
        provider_profile_identity: str,
        evidence_service: WmsCallEvidenceService,
        breaker_service: WmsCircuitBreakerService,
    ) -> None:
        if not provider_profile_identity:
            raise ValueError("provider profile identity is required")
        self._session_factory = session_factory
        self._provider_profile_identity = provider_profile_identity
        self._evidence_service = evidence_service
        self._breaker_service = breaker_service

    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        async with self._session_factory() as db:
            try:
                decision = await self._breaker_service.before_call(
                    db,
                    target_code=target_code,
                    operation_name=operation_identity,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return WmsQueryCallPermit(
            allowed=decision.allowed,
            reason=decision.reason,
            retry_after_seconds=decision.retry_after_seconds,
            probe_generation=decision.probe_generation,
        )

    async def record(
        self,
        *,
        operation_identity: str,
        target_code: str,
        request_snapshot: Mapping[str, object],
        outcome: object,
        permit: WmsQueryCallPermit,
    ) -> str:
        evidence_key = f"status:{operation_identity}:{uuid4().hex}"
        status = WmsEvidenceStatus.SUCCEEDED if isinstance(outcome, QuerySuccess) else WmsEvidenceStatus.FAILED
        reason_code = getattr(outcome, "reason_code", None)
        retryable = outcome.retryable if isinstance(outcome, QueryTechnicalFailure) else False
        async with self._session_factory() as db:
            try:
                evidence = await self._evidence_service.record_sync_call(
                    db,
                    evidence_key=evidence_key,
                    provider_profile_identity=self._provider_profile_identity,
                    operation_name=operation_identity,
                    target_code=target_code,
                    status=status,
                    request_snapshot=dict(request_snapshot),
                    response_snapshot=_outcome_snapshot(outcome),
                    reason_code=reason_code,
                    retryable=retryable,
                )
                if permit.allowed:
                    record_breaker = (
                        self._breaker_service.record_success
                        if isinstance(outcome, (QuerySuccess, QueryBusinessReject))
                        else self._breaker_service.record_failure
                    )
                    await record_breaker(
                        db,
                        target_code=target_code,
                        operation_name=operation_identity,
                        evidence_key=evidence.evidence_key,
                        probe_generation=permit.probe_generation,
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return evidence.evidence_key


def _outcome_snapshot(outcome: object) -> dict[str, Any]:
    if isinstance(outcome, QuerySuccess):
        value = outcome.value
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", exclude_none=True)
        return {"result_type": type(value).__name__}
    if isinstance(outcome, (QueryBusinessReject, QueryTechnicalFailure)):
        return {key: value for key, value in asdict(outcome).items() if value is not None and key != "evidence_key"}
    return {"outcome_type": type(outcome).__name__}


def classify_source_version(
    *,
    previous_version: str,
    previous_response_hash: str,
    source_version: str,
    response_hash: str,
) -> str | None:
    """按同一 authority/request 的最近成功证据执行确定性单调校验。"""

    if source_version == previous_version:
        if response_hash != previous_response_hash:
            return "WMS_SOURCE_VERSION_PAYLOAD_CONFLICT"
        return None
    if not source_version.isdecimal() or not previous_version.isdecimal():
        return "WMS_SOURCE_VERSION_NOT_COMPARABLE"
    if int(source_version) < int(previous_version):
        return "WMS_SOURCE_VERSION_REGRESSION"
    return None


class WmsRegistryCallEvidenceWriter:
    """使用现有 evidence repository 写入 registry QUERY 的脱敏证据。"""

    def __init__(
        self,
        *,
        session_factory,
        evidence_service: WmsCallEvidenceService,
        breaker_service: WmsCircuitBreakerService,
    ) -> None:
        self._session_factory = session_factory
        self._evidence_service = evidence_service
        self._breaker_service = breaker_service

    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        async with self._session_factory() as db:
            try:
                decision = await self._breaker_service.before_call(
                    db,
                    target_code=target_code,
                    operation_name=operation_identity,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return WmsQueryCallPermit(
            allowed=decision.allowed,
            reason=decision.reason,
            retry_after_seconds=decision.retry_after_seconds,
            probe_generation=decision.probe_generation,
        )

    async def validate_source_version(
        self,
        *,
        operation_identity: str,
        target_code: str,
        profile_identity: str,
        request_canonical_hash: str,
        source_version: str,
        response_hash: str,
    ) -> str | None:
        del target_code
        async with self._session_factory() as db:
            previous = await self._evidence_service.repo.get_latest_query_success(
                db,
                provider_profile_identity=profile_identity,
                operation_name=operation_identity,
                request_canonical_hash=request_canonical_hash,
            )
        if previous is None:
            return None
        previous_version = previous.response_snapshot.get("source_version")
        previous_response_hash = previous.response_snapshot.get("typed_response_hash")
        if not isinstance(previous_version, str) or not isinstance(previous_response_hash, str):
            return "WMS_SOURCE_VERSION_HISTORY_INVALID"
        return classify_source_version(
            previous_version=previous_version,
            previous_response_hash=previous_response_hash,
            source_version=source_version,
            response_hash=response_hash,
        )

    async def record(
        self,
        *,
        operation_identity: str,
        target_code: str,
        profile_identity: str,
        profile_digest: str,
        endpoint_digest: str,
        request_snapshot: Mapping[str, object],
        request_canonical_hash: str,
        response_hash: str | None,
        attempt_count: int,
        http_status: int | None,
        outcome: object,
        permit: WmsQueryCallPermit,
    ) -> str:
        evidence_key = f"query:{operation_identity}:{uuid4().hex}"
        status = WmsEvidenceStatus.SUCCEEDED if isinstance(outcome, QuerySuccess) else WmsEvidenceStatus.FAILED
        response_snapshot: dict[str, object] = {
            "outcome_kind": type(outcome).__name__,
            "attempt_count": attempt_count,
        }
        if response_hash is not None:
            response_snapshot["typed_response_hash"] = response_hash
        if isinstance(outcome, QuerySuccess):
            source_version = getattr(outcome.value, "source_version", None)
            if source_version is not None:
                response_snapshot["source_version"] = str(source_version)
        request_evidence = {
            **request_snapshot,
            "profile_digest": profile_digest,
            "endpoint_digest": endpoint_digest,
            "request_canonical_hash": request_canonical_hash,
        }
        reason_code = getattr(outcome, "reason_code", None)
        retryable = outcome.retryable if isinstance(outcome, QueryTechnicalFailure) else False
        async with self._session_factory() as db:
            try:
                evidence = await self._evidence_service.record_sync_call(
                    db,
                    evidence_key=evidence_key,
                    provider_profile_identity=profile_identity,
                    operation_name=operation_identity,
                    target_code=target_code,
                    status=status,
                    request_snapshot=request_evidence,
                    response_snapshot=response_snapshot,
                    http_status=http_status,
                    reason_code=reason_code,
                    retryable=retryable,
                    finished_at=timezone.now_for_db(),
                )
                if permit.allowed:
                    record_breaker = (
                        self._breaker_service.record_success
                        if isinstance(outcome, (QuerySuccess, QueryBusinessReject))
                        else self._breaker_service.record_failure
                    )
                    await record_breaker(
                        db,
                        target_code=target_code,
                        operation_name=operation_identity,
                        evidence_key=evidence.evidence_key,
                        probe_generation=permit.probe_generation,
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return evidence.evidence_key


__all__ = [
    "WmsEffectStatusCallEvidenceWriter",
    "WmsEffectStatusEvidenceWriter",
    "WmsQueryCallPermit",
    "WmsRegistryCallEvidenceWriter",
    "classify_source_version",
]
