"""无 operation 分支的 WMS QUERY transport executor。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import zlib
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, TypeVar
from urllib.parse import urljoin
from uuid import uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from src.app.runtime.orchestration.operation_observability import (
    NorthboundOutcome,
    emit_northbound_operation_observation,
)
from src.app.runtime.system_capabilities.wms.contracts import (
    OutboundAuthScheme,
    WmsOperationMode,
    WmsProviderOperationBinding,
    WmsTransportBudget,
)
from src.app.wms_integration.models import WmsEvidenceStatus
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
    WmsQueryOutcome,
)
from src.app.wms_integration.transport_url import validate_wms_base_url
from src.core.logger import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import BaseModel

    from src.app.runtime.system_capabilities.wms.contracts import WmsOperationContract
    from src.app.wms_integration.services.circuit_breaker_service import WmsCircuitBreakerService
    from src.app.wms_integration.services.evidence_service import WmsCallEvidenceService

QueryResultT = TypeVar("QueryResultT")


class _QueryBudgetViolation(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class _MalformedProviderResponse(Exception):
    pass


class _ContentEncodingFailure(Exception):
    pass


class _CredentialResolutionFailure(Exception):
    pass


def _query_observability_outcome(
    outcome: object,
) -> NorthboundOutcome:
    if isinstance(outcome, QuerySuccess):
        return "SUCCESS"
    if isinstance(outcome, QueryBusinessReject):
        return "BUSINESS_REJECT"
    if isinstance(outcome, QueryTechnicalFailure):
        return "TECHNICAL_FAILURE"
    return "CONTRACT_FAILURE"


@dataclass(frozen=True, slots=True)
class WmsBoundQueryEndpoint:
    """Attempt pin 的 Provider operation binding 与部署 origin 的组合。"""

    binding: WmsProviderOperationBinding
    base_url: str

    def __post_init__(self) -> None:
        if self.binding.operation.mode is not WmsOperationMode.QUERY:
            raise ValueError("query endpoint requires a QUERY operation binding")
        parsed = validate_wms_base_url(self.base_url)
        if self.binding.profile.environment == "production" and parsed.scheme.lower() != "https":
            raise ValueError("production WMS provider endpoint requires HTTPS")

    @property
    def url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", self.binding.operation.endpoint_path.lstrip("/"))


class WmsCredentialProvider(Protocol):
    """只以版本化 reference 解析密钥材料；调用者不得持久化返回值。"""

    def resolve(self, credential_reference: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class WmsQueryCallPermit:
    """QUERY 调用持有的 breaker 准入凭据。"""

    allowed: bool
    reason: str | None = None
    retry_after_seconds: float | None = None
    probe_generation: int | None = None


class WmsQueryEvidenceWriter(Protocol):
    """QUERY breaker 生命周期与 evidence 必写边界。"""

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


class WmsCallEvidenceQueryWriter:
    """以独立短事务把封闭 QUERY outcome 写入现有 evidence 主账。"""

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
        evidence_key = f"query:{operation_identity}:{uuid4().hex}"
        status = WmsEvidenceStatus.SUCCEEDED if isinstance(outcome, QuerySuccess) else WmsEvidenceStatus.FAILED
        reason_code = getattr(outcome, "reason_code", None)
        retryable = outcome.retryable if isinstance(outcome, QueryTechnicalFailure) else False
        response_snapshot = _outcome_snapshot(outcome)
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
                    response_snapshot=response_snapshot,
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


class WmsQueryTransportExecutor:
    """仅按 operation contract 执行 HTTP，不识别具体 operation identity。"""

    def __init__(
        self,
        *,
        endpoint: WmsBoundQueryEndpoint,
        transport: httpx.AsyncBaseTransport | None,
        evidence_writer: WmsQueryEvidenceWriter,
        credential_provider: WmsCredentialProvider,
        now: Callable[[], datetime] | None = None,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._transport = transport
        self._evidence_writer = evidence_writer
        self._credential_provider = credential_provider
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory or (lambda: uuid4().hex)

    @property
    def binding(self) -> WmsProviderOperationBinding:
        return self._endpoint.binding

    async def execute(
        self,
        *,
        request: BaseModel,
        provider_payload: Mapping[str, object],
        map_success: Callable[[Any], QueryResultT],
    ) -> WmsQueryOutcome[QueryResultT]:
        started_at = time.perf_counter()
        contract = self.binding.operation
        permit = WmsQueryCallPermit(allowed=False, reason="BREAKER_STATE_UNAVAILABLE")
        try:
            permit = await self._evidence_writer.before_call(
                operation_identity=contract.identity,
                target_code=contract.target_code,
            )
            if not permit.allowed:
                outcome = QueryTechnicalFailure(
                    reason_code="WMS_CIRCUIT_OPEN",
                    message="WMS QUERY circuit breaker rejected the call",
                    retryable=True,
                    retry_after_seconds=permit.retry_after_seconds,
                )
                return await self._record_and_observe(
                    contract=contract,
                    request=request,
                    outcome=outcome,
                    permit=permit,
                    started_at=started_at,
                )
            try:
                # timeout_seconds 是整次 QUERY（全部 attempts、分页与退避）的总预算。
                async with asyncio.timeout(contract.budget.timeout_seconds):
                    for attempt_index in range(contract.retry_policy.max_attempts):
                        try:
                            outcome = await self._execute_with_deadline(
                                provider_payload=provider_payload,
                                map_success=map_success,
                            )
                        except httpx.TimeoutException:
                            outcome = QueryTechnicalFailure(
                                reason_code="WMS_PROVIDER_TIMEOUT",
                                message="WMS QUERY deadline exceeded",
                                retryable=True,
                            )
                        except httpx.RequestError:
                            outcome = QueryTechnicalFailure(
                                reason_code="WMS_PROVIDER_UNAVAILABLE",
                                message="WMS QUERY transport unavailable",
                                retryable=True,
                            )
                        if not isinstance(outcome, QueryTechnicalFailure) or not outcome.retryable:
                            break
                        if attempt_index + 1 >= contract.retry_policy.max_attempts:
                            break
                        await asyncio.sleep(contract.retry_policy.backoff_seconds[attempt_index])
            except TimeoutError:
                outcome = QueryTechnicalFailure(
                    reason_code="WMS_PROVIDER_TIMEOUT",
                    message="WMS QUERY deadline exceeded",
                    retryable=True,
                )
        except _QueryBudgetViolation as exc:
            outcome = QueryContractFailure(reason_code=exc.reason_code, message=exc.message)
        except _ContentEncodingFailure:
            outcome = QueryContractFailure(
                reason_code="WMS_CONTENT_ENCODING_INVALID",
                message="WMS QUERY response content encoding is malformed",
            )
        except _CredentialResolutionFailure:
            outcome = QueryContractFailure(
                reason_code="WMS_CREDENTIAL_UNAVAILABLE",
                message="WMS QUERY credential could not be resolved",
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, _MalformedProviderResponse, ValueError):
            outcome = QueryContractFailure(
                reason_code="WMS_MALFORMED_RESPONSE",
                message="WMS QUERY response violates the provider contract",
            )
        except DBAPIError:
            raise
        except Exception:
            outcome = QueryContractFailure(
                reason_code="WMS_UNEXPECTED_TRANSPORT_FAILURE",
                message="unexpected WMS QUERY transport failure",
            )
        return await self._record_and_observe(
            contract=contract,
            request=request,
            outcome=outcome,
            permit=permit,
            started_at=started_at,
        )

    async def _record_and_observe(
        self,
        *,
        contract: WmsOperationContract,
        request: BaseModel,
        outcome: WmsQueryOutcome[QueryResultT],
        permit: WmsQueryCallPermit,
        started_at: float,
    ) -> WmsQueryOutcome[QueryResultT]:
        recorded = await self._record_evidence(
            contract=contract,
            request=request,
            outcome=outcome,
            permit=permit,
        )
        evidence_ref = getattr(recorded, "evidence_key", None) or "UNAVAILABLE"
        try:
            _ = emit_northbound_operation_observation(
                operation_identity=contract.identity,
                provider_profile_identity=self.binding.profile.identity,
                outcome=_query_observability_outcome(recorded),
                latency_ms=(time.perf_counter() - started_at) * 1_000,
                trace_id=evidence_ref,
                correlation_id=evidence_ref,
                evidence_ref=evidence_ref,
                stage="QUERY_EVIDENCE",
            )
        except Exception as exc:  # pragma: no cover - 观测失败不改变 QUERY 领域 outcome
            logger.warning(f"WMS QUERY observability emission failed: {type(exc).__name__}")
        return recorded

    async def _execute_with_deadline(
        self,
        *,
        provider_payload: Mapping[str, object],
        map_success: Callable[[Any], QueryResultT],
    ) -> WmsQueryOutcome[QueryResultT]:
        contract = self.binding.operation
        pagination = contract.pagination
        if pagination is None:
            raise _MalformedProviderResponse("QUERY contract is missing pagination semantics")

        cursor: str | None = None
        seen_cursors: set[str] = set()
        aggregate_payload: dict[str, Any] | None = None
        cumulative_wire_bytes = 0
        cumulative_decoded_bytes = 0
        cumulative_rows = 0

        async with httpx.AsyncClient(
            transport=self._transport,
            trust_env=False,
            timeout=contract.budget.timeout_seconds,
        ) as client:
            for _page_number in range(1, pagination.max_pages + 1):
                page_payload = dict(provider_payload)
                if cursor is not None:
                    page_payload[pagination.request_cursor_field] = cursor
                outbound_request = client.build_request(
                    contract.http_method.value,
                    self._endpoint.url,
                    **({"params": page_payload} if contract.http_method.value == "GET" else {"json": page_payload}),
                )
                self._authenticate(outbound_request)
                response = await client.send(outbound_request, stream=True)
                try:
                    raw_body, cumulative_wire_bytes = await _read_bounded_wire_body(
                        response,
                        max_chunk_bytes=contract.budget.max_chunk_bytes,
                        max_wire_bytes=contract.budget.max_wire_bytes,
                        cumulative_wire_bytes=cumulative_wire_bytes,
                    )
                    status_failure = _classify_http_failure(response.status_code, None, response.headers)
                    if status_failure is not None and (
                        response.status_code in {401, 403, 408, 429} or not (400 <= response.status_code < 500)
                    ):
                        return status_failure
                    if 400 <= response.status_code < 500:
                        payload = _parse_optional_failure_body(
                            raw_body,
                            content_encoding=response.headers.get("content-encoding", "identity"),
                            budget=contract.budget,
                        )
                        return _classify_http_failure(
                            response.status_code, payload, response.headers
                        ) or QueryTechnicalFailure(
                            reason_code="WMS_PROVIDER_CLIENT_ERROR",
                            message="WMS QUERY provider returned a client error",
                            retryable=False,
                        )
                    decoded_body = _decode_bounded_body(
                        raw_body,
                        content_encoding=response.headers.get("content-encoding", "identity"),
                        allowed_content_encodings=contract.budget.allowed_content_encodings,
                        max_decoded_bytes=contract.budget.max_decoded_bytes - cumulative_decoded_bytes,
                        max_compression_ratio=contract.budget.max_compression_ratio,
                    )
                    cumulative_decoded_bytes += len(decoded_body)
                    parsed = _parse_bounded_json(
                        decoded_body,
                        max_depth=contract.budget.max_json_depth,
                        max_field_length=contract.budget.max_field_length,
                    )
                finally:
                    await response.aclose()
                if not isinstance(parsed, dict):
                    raise _MalformedProviderResponse("successful QUERY response must be an object")
                page_rows = parsed.get(pagination.response_items_field)
                if not isinstance(page_rows, list):
                    raise _MalformedProviderResponse("successful QUERY response must contain an items array")
                cumulative_rows += len(page_rows)
                if contract.budget.max_rows is not None and cumulative_rows > contract.budget.max_rows:
                    raise _QueryBudgetViolation("WMS_ROW_BUDGET_EXCEEDED", "WMS QUERY row budget exceeded")

                if aggregate_payload is None:
                    aggregate_payload = dict(parsed)
                    aggregate_payload[pagination.response_items_field] = list(page_rows)
                else:
                    _merge_page_metadata(
                        aggregate_payload,
                        parsed,
                        cursor_field=pagination.response_cursor_field,
                        items_field=pagination.response_items_field,
                    )
                    aggregate_payload[pagination.response_items_field].extend(page_rows)

                raw_cursor = parsed.get(pagination.response_cursor_field)
                if raw_cursor is None:
                    aggregate_payload.pop(pagination.response_cursor_field, None)
                    return QuerySuccess(map_success(aggregate_payload))
                if not isinstance(raw_cursor, str) or not raw_cursor.strip():
                    raise _MalformedProviderResponse("pagination cursor must be a non-empty string")
                cursor = raw_cursor.strip()
                if cursor in seen_cursors:
                    raise _MalformedProviderResponse("pagination cursor cycle detected")
                seen_cursors.add(cursor)

        raise _QueryBudgetViolation("WMS_PAGE_BUDGET_EXCEEDED", "WMS QUERY page budget exceeded")

    def _authenticate(self, request: httpx.Request) -> None:
        auth = self.binding.outbound_auth
        if auth.scheme is not OutboundAuthScheme.HMAC_SHA256 or auth.credential_reference is None:
            raise _CredentialResolutionFailure
        try:
            secret = self._credential_provider.resolve(auth.credential_reference)
        except Exception as exc:
            raise _CredentialResolutionFailure from exc
        if not isinstance(secret, bytes) or not secret:
            raise _CredentialResolutionFailure
        timestamp = str(int(self._now().timestamp()))
        nonce = self._nonce_factory()
        if not isinstance(nonce, str) or not nonce:
            raise _CredentialResolutionFailure
        body_hash = hashlib.sha256(request.content).hexdigest()
        canonical = "\n".join(
            (
                request.method,
                request.url.raw_path.decode("ascii"),
                timestamp,
                nonce,
                body_hash,
            )
        )
        signature = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        request.headers.update(
            {
                "X-WMS-Credential-Reference": auth.credential_reference,
                "X-WMS-Signature-Algorithm": auth.scheme.value,
                "X-WMS-Timestamp": timestamp,
                "X-WMS-Nonce": nonce,
                "X-WMS-Content-SHA256": body_hash,
                "X-WMS-Signature": signature,
            }
        )

    async def _record_evidence(
        self,
        *,
        contract: WmsOperationContract,
        request: BaseModel,
        outcome: WmsQueryOutcome[QueryResultT],
        permit: WmsQueryCallPermit,
    ) -> WmsQueryOutcome[QueryResultT]:
        try:
            evidence_key = await self._evidence_writer.record(
                operation_identity=contract.identity,
                target_code=contract.target_code,
                request_snapshot=request.model_dump(mode="json", exclude_none=True),
                outcome=outcome,
                permit=permit,
            )
        except DBAPIError:
            raise
        except Exception:
            return QueryContractFailure(
                reason_code="WMS_EVIDENCE_WRITE_FAILED",
                message="WMS QUERY evidence could not be persisted",
            )
        if not isinstance(evidence_key, str) or not evidence_key.strip():
            return QueryContractFailure(
                reason_code="WMS_EVIDENCE_WRITE_FAILED",
                message="WMS QUERY evidence writer returned no key",
            )
        return replace(outcome, evidence_key=evidence_key)


async def _read_bounded_wire_body(
    response: httpx.Response,
    *,
    max_chunk_bytes: int,
    max_wire_bytes: int,
    cumulative_wire_bytes: int,
) -> tuple[bytes, int]:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise _MalformedProviderResponse("invalid Content-Length") from exc
        if declared_length < 0:
            raise _MalformedProviderResponse("negative Content-Length")
        if cumulative_wire_bytes + declared_length > max_wire_bytes:
            raise _QueryBudgetViolation("WMS_WIRE_BUDGET_EXCEEDED", "WMS QUERY wire budget exceeded")

    body = bytearray()
    chunks = (response.content,) if response.is_stream_consumed else response.aiter_raw()
    async for chunk in _as_async_chunks(chunks):
        if len(chunk) > max_chunk_bytes:
            raise _QueryBudgetViolation("WMS_CHUNK_BUDGET_EXCEEDED", "WMS QUERY chunk budget exceeded")
        cumulative_wire_bytes += len(chunk)
        if cumulative_wire_bytes > max_wire_bytes:
            raise _QueryBudgetViolation("WMS_WIRE_BUDGET_EXCEEDED", "WMS QUERY wire budget exceeded")
        body.extend(chunk)
    return bytes(body), cumulative_wire_bytes


async def _as_async_chunks(chunks):
    if hasattr(chunks, "__aiter__"):
        async for chunk in chunks:
            yield chunk
        return
    for chunk in chunks:
        yield chunk


def _decode_bounded_body(
    raw_body: bytes,
    *,
    content_encoding: str,
    allowed_content_encodings: tuple[str, ...],
    max_decoded_bytes: int,
    max_compression_ratio: float,
) -> bytes:
    encoding = content_encoding.strip().lower() or "identity"
    if encoding not in allowed_content_encodings:
        raise _QueryBudgetViolation(
            "WMS_UNSUPPORTED_CONTENT_ENCODING",
            f"unsupported WMS content encoding: {encoding}",
        )
    if max_decoded_bytes <= 0:
        raise _QueryBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
    if encoding == "identity":
        decoded = raw_body
    else:
        window_bits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        decoder = zlib.decompressobj(window_bits)
        try:
            decoded = decoder.decompress(raw_body, max_decoded_bytes + 1)
            if decoder.unconsumed_tail or len(decoded) > max_decoded_bytes:
                raise _QueryBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
            decoded += decoder.flush(max_decoded_bytes - len(decoded) + 1)
        except zlib.error as exc:
            raise _ContentEncodingFailure from exc
        if not decoder.eof or decoder.unused_data:
            raise _ContentEncodingFailure
    if len(decoded) > max_decoded_bytes:
        raise _QueryBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
    if raw_body and len(decoded) / len(raw_body) > max_compression_ratio:
        raise _QueryBudgetViolation(
            "WMS_COMPRESSION_RATIO_EXCEEDED",
            "WMS QUERY compression ratio budget exceeded",
        )
    return decoded


def _parse_bounded_json(decoded_body: bytes, *, max_depth: int, max_field_length: int) -> Any:
    parsed = json.loads(decoded_body)
    _validate_json_structure(parsed, max_depth=max_depth, max_field_length=max_field_length)
    return parsed


def _validate_json_structure(value: Any, *, max_depth: int, max_field_length: int, depth: int = 1) -> None:
    if depth > max_depth:
        raise _QueryBudgetViolation("WMS_JSON_DEPTH_EXCEEDED", "WMS QUERY JSON depth budget exceeded")
    if isinstance(value, str):
        if len(value) > max_field_length:
            raise _QueryBudgetViolation(
                "WMS_JSON_FIELD_LENGTH_EXCEEDED",
                "WMS QUERY JSON field length budget exceeded",
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if len(str(key)) > max_field_length:
                raise _QueryBudgetViolation(
                    "WMS_JSON_FIELD_LENGTH_EXCEEDED",
                    "WMS QUERY JSON field length budget exceeded",
                )
            _validate_json_structure(
                item,
                max_depth=max_depth,
                max_field_length=max_field_length,
                depth=depth + 1,
            )
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_structure(
                item,
                max_depth=max_depth,
                max_field_length=max_field_length,
                depth=depth + 1,
            )


def _classify_http_failure(  # noqa: PLR0911 - HTTP 状态封闭矩阵保持逐分支可审计。
    status_code: int,
    payload: Any,
    headers: Mapping[str, str],
) -> QueryBusinessReject | QueryTechnicalFailure | None:
    if 200 <= status_code < 300:
        return None
    if status_code == 408:
        return QueryTechnicalFailure(
            reason_code="WMS_PROVIDER_TIMEOUT",
            message="WMS QUERY provider timed out the request",
            retryable=True,
        )
    if status_code == 429:
        return QueryTechnicalFailure(
            reason_code="WMS_RATE_LIMITED",
            message="WMS rate limited the QUERY request",
            retryable=True,
            retry_after_seconds=_parse_retry_after(headers.get("retry-after")),
        )
    if status_code == 401:
        return QueryTechnicalFailure(
            reason_code="WMS_AUTHENTICATION_FAILED",
            message="WMS QUERY authentication failed",
            retryable=False,
        )
    if status_code == 403:
        return QueryTechnicalFailure(
            reason_code="WMS_AUTHORIZATION_FAILED",
            message="WMS QUERY authorization failed",
            retryable=False,
        )
    if 400 <= status_code < 500:
        if isinstance(payload, dict) and payload.get("classification") == "BUSINESS_REJECT":
            reason_code = _payload_text(payload, "reason_code", "")
            if reason_code:
                return QueryBusinessReject(
                    reason_code=reason_code,
                    message=_payload_text(payload, "message", "WMS rejected the QUERY request"),
                )
        return QueryTechnicalFailure(
            reason_code="WMS_PROVIDER_CLIENT_ERROR",
            message="WMS QUERY provider returned a client error",
            retryable=False,
        )
    if status_code >= 500:
        return QueryTechnicalFailure(
            reason_code="WMS_UNAVAILABLE",
            message="WMS QUERY service unavailable",
            retryable=True,
        )
    return QueryTechnicalFailure(
        reason_code="WMS_UNEXPECTED_HTTP_STATUS",
        message="WMS QUERY provider returned an unexpected HTTP status",
        retryable=False,
    )


def _parse_optional_failure_body(
    raw_body: bytes,
    *,
    content_encoding: str,
    budget: WmsTransportBudget,
) -> Any | None:
    """4xx body 仅用于识别显式业务拒绝；任何解析失败都保持 technical。"""

    if not raw_body:
        return None
    try:
        decoded = _decode_bounded_body(
            raw_body,
            content_encoding=content_encoding,
            allowed_content_encodings=budget.allowed_content_encodings,
            max_decoded_bytes=budget.max_decoded_bytes,
            max_compression_ratio=budget.max_compression_ratio,
        )
        return _parse_bounded_json(
            decoded,
            max_depth=budget.max_json_depth,
            max_field_length=budget.max_field_length,
        )
    except (
        _ContentEncodingFailure,
        _MalformedProviderResponse,
        _QueryBudgetViolation,
        UnicodeDecodeError,
        ValueError,
    ):
        return None


def _payload_text(payload: Any, key: str, default: str) -> str:
    if not isinstance(payload, dict):
        return default
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _merge_page_metadata(
    aggregate: dict[str, Any],
    page: dict[str, Any],
    *,
    cursor_field: str,
    items_field: str,
) -> None:
    for key, value in page.items():
        if key in {cursor_field, items_field} or value is None:
            continue
        existing = aggregate.get(key)
        if existing is not None and existing != value:
            raise _MalformedProviderResponse(f"pagination metadata changed for field: {key}")
        aggregate[key] = value


def _outcome_snapshot(outcome: object) -> dict[str, Any]:
    if isinstance(outcome, QuerySuccess):
        value = outcome.value
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", exclude_none=True)
        return {"result_type": type(value).__name__}
    if isinstance(outcome, (QueryBusinessReject, QueryTechnicalFailure, QueryContractFailure)):
        return {key: value for key, value in asdict(outcome).items() if value is not None and key != "evidence_key"}
    return {"outcome_type": type(outcome).__name__}


__all__ = [
    "WmsBoundQueryEndpoint",
    "WmsCallEvidenceQueryWriter",
    "WmsCredentialProvider",
    "WmsQueryCallPermit",
    "WmsQueryEvidenceWriter",
    "WmsQueryTransportExecutor",
]
