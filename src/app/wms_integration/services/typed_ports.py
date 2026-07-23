"""WMS 同步 typed ports。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeVar
from urllib.parse import quote

from pydantic import BaseModel, ValidationError

from src.app.wms_integration.models import (
    ConfirmOutboundRequest,
    ConfirmOutboundResponse,
    ReleaseReservationRequest,
    ReleaseReservationResponse,
    ReserveInventoryRequest,
    ReserveInventoryResponse,
    WmsEvidenceStatus,
    WmsOperationName,
    WmsPortRequest,
)
from src.app.wms_integration.services.circuit_breaker_service import (
    WmsCircuitBreakerDecision,
    WmsCircuitBreakerService,
    wms_circuit_breaker_service,
)
from src.app.wms_integration.services.endpoint_config import (
    WmsEndpointConfig,
    WmsOperationEndpoint,
    wms_endpoint_config,
)
from src.app.wms_integration.services.evidence_service import (
    WmsCallEvidenceService,
    wms_call_evidence_service,
)
from src.app.wms_integration.services.exceptions import (
    WmsBusinessRejectedError,
    WmsCircuitOpenError,
    WmsEvidencePersistenceError,
    WmsIntegrationError,
    WmsUnavailableError,
)
from src.app.wms_integration.services.http_client import (
    WmsHttpClient,
    WmsHttpResult,
    wms_http_client,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.wms_integration.models import WmsCallEvidence

ResponseT = TypeVar("ResponseT", bound=BaseModel)
EvidenceKeyFactory = Callable[[WmsOperationName, str], str]
WmsTypedPortObservabilityEmit = Callable[[str, dict[str, object]], object]


class WmsSessionFactory(Protocol):
    """为 WMS typed ports 提供短事务 session。"""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


class WmsTypedPortService:
    """WMS 同步 typed ports。

    事务契约：
    - breaker `before_call()` 在短事务中执行并提交；
    - HTTP 调用发生在事务外；
    - evidence 与 breaker `record_*()` 在调用完成后的独立短事务中执行。
    """

    def __init__(
        self,
        *,
        session_factory: WmsSessionFactory,
        endpoint_config: WmsEndpointConfig | None = None,
        http_client: WmsHttpClient | None = None,
        evidence_service: WmsCallEvidenceService | None = None,
        breaker_service: WmsCircuitBreakerService | None = None,
        evidence_key_factory: EvidenceKeyFactory | None = None,
        observability_emit: WmsTypedPortObservabilityEmit | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.endpoint_config = endpoint_config or wms_endpoint_config
        self.http_client = http_client or wms_http_client
        self.evidence_service = evidence_service or wms_call_evidence_service
        self.breaker_service = breaker_service or wms_circuit_breaker_service
        self.evidence_key_factory = evidence_key_factory or _default_evidence_key
        self._observability_emit = observability_emit

    async def reserve_inventory(self, request: ReserveInventoryRequest) -> ReserveInventoryResponse:
        return await self._execute("reserve_inventory", request, ReserveInventoryResponse)

    async def release_reservation(self, request: ReleaseReservationRequest) -> ReleaseReservationResponse:
        return await self._execute("release_reservation", request, ReleaseReservationResponse)

    async def confirm_outbound(self, request: ConfirmOutboundRequest) -> ConfirmOutboundResponse:
        return await self._execute("confirm_outbound", request, ConfirmOutboundResponse)

    async def _execute(
        self,
        operation_name: WmsOperationName,
        request: WmsPortRequest,
        response_model: type[ResponseT],
    ) -> ResponseT:
        endpoint = self.endpoint_config.resolve(operation_name)
        evidence_key = self.evidence_key_factory(operation_name, request.request_id)
        request_payload = request.model_dump(mode="json", exclude_none=True)
        started_at = timezone.now_for_db()

        decision = await self._before_call(endpoint, trace_id=request.trace_id)
        if not decision.allowed:
            evidence = await self._record_evidence(
                endpoint=endpoint,
                evidence_key=evidence_key,
                request=request,
                request_payload=request_payload,
                status=WmsEvidenceStatus.FAILED,
                response_snapshot={
                    "error": "WMS circuit breaker open",
                    "reason": decision.reason,
                    "retry_after_seconds": decision.retry_after_seconds,
                },
                reason_code="WMS_CIRCUIT_OPEN",
                retryable=True,
                started_at=started_at,
            )
            raise WmsCircuitOpenError(
                "WMS 熔断器已打开",
                operation_name=operation_name,
                evidence_key=evidence.evidence_key,
                reason_code="WMS_CIRCUIT_OPEN",
                retryable=True,
                target_code=endpoint.target_code,
            )

        try:
            http_result = await self._send_http(endpoint, request_payload)
        except WmsIntegrationError as exc:
            evidence = await self._record_failure_and_breaker(
                endpoint=endpoint,
                evidence_key=evidence_key,
                request=request,
                request_payload=request_payload,
                response_snapshot={
                    "error": exc.message,
                    "reason_code": exc.reason_code,
                },
                reason_code=exc.reason_code,
                http_status=exc.http_status,
                probe_generation=decision.probe_generation,
                started_at=started_at,
            )
            raise exc.with_evidence_key(evidence.evidence_key) from exc

        if 200 <= http_result.status_code < 300:
            return await self._handle_success(
                endpoint=endpoint,
                evidence_key=evidence_key,
                request=request,
                request_payload=request_payload,
                http_result=http_result,
                response_model=response_model,
                probe_generation=decision.probe_generation,
                started_at=started_at,
            )
        if 400 <= http_result.status_code < 500:
            evidence = await self._record_business_reject_and_breaker_success(
                endpoint=endpoint,
                evidence_key=evidence_key,
                request=request,
                request_payload=request_payload,
                response_snapshot=_snapshot_payload(http_result.payload),
                http_status=http_result.status_code,
                reason_code=_extract_reason_code(http_result.payload, default="WMS_BUSINESS_REJECTED"),
                probe_generation=decision.probe_generation,
                started_at=started_at,
            )
            raise WmsBusinessRejectedError(
                _extract_message(http_result.payload, default="WMS 业务拒绝"),
                operation_name=operation_name,
                evidence_key=evidence.evidence_key,
                http_status=http_result.status_code,
                reason_code=evidence.reason_code,
                retryable=False,
                target_code=endpoint.target_code,
            )

        evidence = await self._record_failure_and_breaker(
            endpoint=endpoint,
            evidence_key=evidence_key,
            request=request,
            request_payload=request_payload,
            response_snapshot=_snapshot_payload(http_result.payload),
            reason_code=_extract_reason_code(http_result.payload, default="WMS_UNAVAILABLE"),
            http_status=http_result.status_code,
            probe_generation=decision.probe_generation,
            started_at=started_at,
        )
        raise WmsUnavailableError(
            _extract_message(http_result.payload, default="WMS 依赖不可用"),
            operation_name=operation_name,
            evidence_key=evidence.evidence_key,
            http_status=http_result.status_code,
            reason_code=evidence.reason_code,
            retryable=True,
            target_code=endpoint.target_code,
        )

    async def _send_http(self, endpoint: WmsOperationEndpoint, request_payload: dict[str, Any]) -> WmsHttpResult:
        if endpoint.http_method == "GET":
            return await self.http_client.get_json(endpoint, request_payload)
        if endpoint.http_method == "POST":
            return await self.http_client.post_json(endpoint, request_payload)
        if endpoint.http_method == "DELETE":
            return await self.http_client.delete(_endpoint_with_path_params(endpoint, request_payload))
        raise WmsUnavailableError(
            "WMS 同步端口 HTTP 方法不受支持",
            operation_name=endpoint.operation_name,
            evidence_key=None,
            reason_code="WMS_UNSUPPORTED_HTTP_METHOD",
            retryable=False,
            target_code=endpoint.target_code,
        )

    async def _handle_success(
        self,
        *,
        endpoint: WmsOperationEndpoint,
        evidence_key: str,
        request: WmsPortRequest,
        request_payload: dict[str, Any],
        http_result: WmsHttpResult,
        response_model: type[ResponseT],
        probe_generation: int | None,
        started_at: Any,
    ) -> ResponseT:
        response_payload = _unwrap_response_payload(http_result.payload, response_model=response_model)
        try:
            response = response_model.model_validate(response_payload)
        except ValidationError as exc:
            evidence = await self._record_failure_and_breaker(
                endpoint=endpoint,
                evidence_key=evidence_key,
                request=request,
                request_payload=request_payload,
                response_snapshot=_snapshot_payload(http_result.payload),
                reason_code="WMS_RESPONSE_PARSE_ERROR",
                http_status=http_result.status_code,
                probe_generation=probe_generation,
                started_at=started_at,
            )
            raise WmsUnavailableError(
                "WMS 响应结构无法解析",
                operation_name=endpoint.operation_name,
                evidence_key=evidence.evidence_key,
                http_status=http_result.status_code,
                reason_code="WMS_RESPONSE_PARSE_ERROR",
                retryable=True,
                target_code=endpoint.target_code,
            ) from exc

        try:
            _ = await self._record_success_and_breaker(
                endpoint=endpoint,
                evidence_key=evidence_key,
                request=request,
                request_payload=request_payload,
                response_snapshot=response.model_dump(mode="json", exclude_none=True),
                http_status=http_result.status_code,
                probe_generation=probe_generation,
                started_at=started_at,
            )
        except Exception as exc:
            self._emit_evidence_persistence_failure(
                endpoint=endpoint,
                request=request,
                evidence_key=evidence_key,
                http_status=http_result.status_code,
            )
            raise WmsEvidencePersistenceError(
                "WMS 已返回成功，但本地 evidence/breaker 成功留痕失败",
                operation_name=endpoint.operation_name,
                evidence_key=evidence_key,
                http_status=http_result.status_code,
                reason_code="WMS_EVIDENCE_PERSISTENCE_FAILED",
                retryable=False,
                target_code=endpoint.target_code,
            ) from exc
        return response

    async def _before_call(self, endpoint: WmsOperationEndpoint, *, trace_id: str | None) -> WmsCircuitBreakerDecision:
        async with self.session_factory() as db:
            try:
                decision = await self.breaker_service.before_call(
                    db,
                    target_code=endpoint.target_code,
                    operation_name=endpoint.operation_name,
                    trace_id=trace_id,
                )
                await db.commit()
                return decision
            except Exception:
                await db.rollback()
                raise

    async def _record_success_and_breaker(
        self,
        *,
        endpoint: WmsOperationEndpoint,
        evidence_key: str,
        request: WmsPortRequest,
        request_payload: dict[str, Any],
        response_snapshot: dict[str, Any],
        http_status: int,
        probe_generation: int | None,
        started_at: Any,
    ) -> WmsCallEvidence:
        async with self.session_factory() as db:
            try:
                evidence = await self._record_evidence_in_session(
                    db,
                    endpoint=endpoint,
                    evidence_key=evidence_key,
                    request=request,
                    request_payload=request_payload,
                    status=WmsEvidenceStatus.SUCCEEDED,
                    response_snapshot=response_snapshot,
                    http_status=http_status,
                    retryable=False,
                    started_at=started_at,
                )
                _ = await self.breaker_service.record_success(
                    db,
                    target_code=endpoint.target_code,
                    operation_name=endpoint.operation_name,
                    evidence_key=evidence.evidence_key,
                    probe_generation=probe_generation,
                    trace_id=request.trace_id,
                )
                await db.commit()
                return evidence
            except Exception:
                await db.rollback()
                raise

    async def _record_business_reject_and_breaker_success(
        self,
        *,
        endpoint: WmsOperationEndpoint,
        evidence_key: str,
        request: WmsPortRequest,
        request_payload: dict[str, Any],
        response_snapshot: dict[str, Any],
        reason_code: str | None,
        http_status: int,
        probe_generation: int | None,
        started_at: Any,
    ) -> WmsCallEvidence:
        async with self.session_factory() as db:
            try:
                evidence = await self._record_evidence_in_session(
                    db,
                    endpoint=endpoint,
                    evidence_key=evidence_key,
                    request=request,
                    request_payload=request_payload,
                    status=WmsEvidenceStatus.FAILED,
                    response_snapshot=response_snapshot,
                    http_status=http_status,
                    reason_code=reason_code,
                    retryable=False,
                    started_at=started_at,
                )
                _ = await self.breaker_service.record_success(
                    db,
                    target_code=endpoint.target_code,
                    operation_name=endpoint.operation_name,
                    evidence_key=evidence.evidence_key,
                    probe_generation=probe_generation,
                    trace_id=request.trace_id,
                )
                await db.commit()
                return evidence
            except Exception:
                await db.rollback()
                raise

    async def _record_failure_and_breaker(
        self,
        *,
        endpoint: WmsOperationEndpoint,
        evidence_key: str,
        request: WmsPortRequest,
        request_payload: dict[str, Any],
        response_snapshot: dict[str, Any],
        reason_code: str | None,
        http_status: int | None,
        probe_generation: int | None,
        started_at: Any,
    ) -> WmsCallEvidence:
        async with self.session_factory() as db:
            try:
                evidence = await self._record_evidence_in_session(
                    db,
                    endpoint=endpoint,
                    evidence_key=evidence_key,
                    request=request,
                    request_payload=request_payload,
                    status=WmsEvidenceStatus.FAILED,
                    response_snapshot=response_snapshot,
                    http_status=http_status,
                    reason_code=reason_code,
                    retryable=True,
                    started_at=started_at,
                )
                _ = await self.breaker_service.record_failure(
                    db,
                    target_code=endpoint.target_code,
                    operation_name=endpoint.operation_name,
                    evidence_key=evidence.evidence_key,
                    probe_generation=probe_generation,
                    trace_id=request.trace_id,
                )
                await db.commit()
                return evidence
            except Exception:
                await db.rollback()
                raise

    async def _record_evidence(
        self,
        *,
        endpoint: WmsOperationEndpoint,
        evidence_key: str,
        request: WmsPortRequest,
        request_payload: dict[str, Any],
        status: WmsEvidenceStatus,
        response_snapshot: dict[str, Any],
        reason_code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        started_at: Any,
    ) -> WmsCallEvidence:
        async with self.session_factory() as db:
            try:
                evidence = await self._record_evidence_in_session(
                    db,
                    endpoint=endpoint,
                    evidence_key=evidence_key,
                    request=request,
                    request_payload=request_payload,
                    status=status,
                    response_snapshot=response_snapshot,
                    reason_code=reason_code,
                    http_status=http_status,
                    retryable=retryable,
                    started_at=started_at,
                )
                await db.commit()
                return evidence
            except Exception:
                await db.rollback()
                raise

    async def _record_evidence_in_session(
        self,
        db: AsyncSession,
        *,
        endpoint: WmsOperationEndpoint,
        evidence_key: str,
        request: WmsPortRequest,
        request_payload: dict[str, Any],
        status: WmsEvidenceStatus,
        response_snapshot: dict[str, Any],
        reason_code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        started_at: Any,
    ) -> WmsCallEvidence:
        return await self.evidence_service.record_sync_call(
            db,
            evidence_key=evidence_key,
            operation_name=endpoint.operation_name,
            target_code=endpoint.target_code,
            status=status,
            request_snapshot=request_payload,
            response_snapshot=response_snapshot,
            request_id=request.request_id,
            trace_id=request.trace_id,
            http_status=http_status,
            reason_code=reason_code,
            retryable=retryable,
            started_at=started_at,
            finished_at=timezone.now_for_db(),
        )

    def _emit_evidence_persistence_failure(
        self,
        *,
        endpoint: WmsOperationEndpoint,
        request: WmsPortRequest,
        evidence_key: str,
        http_status: int,
    ) -> None:
        if self._observability_emit is None or request.trace_id is None:
            return
        _ = self._observability_emit(
            "wms_evidence.persistence_failure",
            {
                "trace_id": request.trace_id,
                "provider_code": endpoint.target_code,
                "operation_kind": endpoint.operation_name,
                "evidence_key": evidence_key,
                "request_id": request.request_id,
                "http_status": http_status,
                "reason_code": "WMS_EVIDENCE_PERSISTENCE_FAILED",
            },
        )


def _default_evidence_key(operation_name: WmsOperationName, request_id: str) -> str:
    return f"sync:{operation_name}:{request_id}"


def _endpoint_with_path_params(
    endpoint: WmsOperationEndpoint,
    request_payload: dict[str, Any],
) -> WmsOperationEndpoint:
    path = endpoint.path
    reservation_key = request_payload.get("reservation_key")
    if reservation_key is not None:
        encoded_reservation_key = quote(str(reservation_key), safe="")
        path = path.replace("{id}", encoded_reservation_key).replace("{reservation_key}", encoded_reservation_key)
    return WmsOperationEndpoint(
        operation_name=endpoint.operation_name,
        http_method=endpoint.http_method,
        target_code=endpoint.target_code,
        base_url=endpoint.base_url,
        path=path,
        timeout=endpoint.timeout,
    )


def _unwrap_response_payload(payload: Any, *, response_model: type[BaseModel]) -> Any:
    if not isinstance(payload, dict) or "data" not in payload or payload.get("data") is None:
        return payload

    data = payload["data"]
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and "items" in response_model.model_fields:
        return {
            **{key: value for key, value in payload.items() if key != "data"},
            "items": data,
        }
    return data


def _snapshot_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {"payload": payload}


def _first_nonempty_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_reason_code(payload: Any, *, default: str) -> str:
    if isinstance(payload, dict):
        reason_code = _first_nonempty_str(payload, ("reason_code", "error_code", "code"))
        if reason_code is not None:
            return reason_code
        error = payload.get("error")
        if isinstance(error, dict):
            reason_code = _first_nonempty_str(error, ("reason_code", "error_code", "code"))
            if reason_code is not None:
                return reason_code
    return default


def _extract_message(payload: Any, *, default: str) -> str:
    if isinstance(payload, dict):
        message = _first_nonempty_str(payload, ("message", "msg", "error_description"))
        if message is not None:
            return message
        error = payload.get("error")
        if isinstance(error, dict):
            message = _first_nonempty_str(error, ("message",))
            if message is not None:
                return message
    return default


__all__ = [
    "WmsSessionFactory",
    "WmsTypedPortService",
]
