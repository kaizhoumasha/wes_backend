"""Registry 驱动、借用长期 client 的 WMS QUERY executor。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import DecimalException
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError

from src.app.sys.canonical_dispatch import CanonicalPayload, ExternalHttpDispatchRequest
from src.app.sys.external_http_binding import (
    ExternalHttpTargetSnapshot,
    FrozenExternalHttpBinding,
)
from src.app.wms_integration.operation_contract import WmsOperationMode
from src.app.wms_integration.ports.operation_common import validate_json_payload
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
    WmsQueryOutcome,
)
from src.app.wms_integration.query_projection import (
    WmsQueryRequestProjection,
    project_wms_query_request,
)
from src.app.wms_integration.query_response import (
    MalformedProviderResponse,
    QueryBudgetViolation,
    classify_http_failure,
    parse_bounded_json,
    parse_optional_failure_body,
)
from src.app.wms_integration.services.http_transport import send_bounded_wms_request
from src.core.bounded_http_response import (
    HttpChunkBudgetExceeded,
    HttpCompressionRatioExceeded,
    HttpContentEncodingFailure,
    HttpDecodedBodyBudgetExceeded,
    HttpResponseContractError,
    HttpUnsupportedContentEncoding,
    HttpWireBudgetExceeded,
    decode_bounded_http_body,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from src.app.wms_integration.endpoint_compiler import CompiledWmsOperationEndpoint
    from src.app.wms_integration.operation_contract import WmsOperationDefinition, WmsPaginationConstraint
    from src.app.wms_integration.query_evidence import WmsQueryCallPermit, WmsQueryEvidenceRecord


class WmsQueryCredentialProvider(Protocol):
    """只按 frozen credential reference 解析 secret。"""

    def resolve(self, credential_reference: str) -> bytes: ...


class WmsRegistryQueryEvidenceWriter(Protocol):
    """统一 QUERY evidence 与 breaker 写边界。"""

    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit: ...

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
    ) -> WmsQueryEvidenceRecord: ...


@dataclass(slots=True)
class _ExecutionBudgetState:
    cumulative_wire_bytes: int = 0
    cumulative_decoded_bytes: int = 0
    attempt_count: int = 0
    http_status: int | None = None
    response_hash: str | None = None


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _target_hash(target: ExternalHttpTargetSnapshot) -> str:
    return _stable_hash(target.as_json())


def _query_payload(projection: WmsQueryRequestProjection) -> dict[str, Any]:
    if projection.json_body is not None:
        return projection.json_body
    payload: dict[str, Any] = {}
    for key, value in projection.query_params:
        existing = payload.get(key)
        if existing is None:
            payload[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            payload[key] = [existing, value]
    return payload


def _rendered_binding(
    frozen_binding: FrozenExternalHttpBinding,
    projection: WmsQueryRequestProjection,
) -> FrozenExternalHttpBinding:
    target = ExternalHttpTargetSnapshot(
        code=frozen_binding.target_snapshot.code,
        url=projection.url,
        http_method=projection.method,
        timeout_seconds=frozen_binding.target_snapshot.timeout_seconds,
    )
    return replace(
        frozen_binding,
        target_snapshot=target,
        target_snapshot_hash=_target_hash(target),
    )


def _build_http_request(
    *,
    client: httpx.AsyncClient,
    projection: WmsQueryRequestProjection,
    frozen_binding: FrozenExternalHttpBinding,
    credential_provider: WmsQueryCredentialProvider,
    now: Callable[[], datetime],
    nonce_factory: Callable[[], str],
) -> httpx.Request:
    payload = CanonicalPayload.from_projection(_query_payload(projection))
    rendered_binding = _rendered_binding(frozen_binding, projection)
    secret: bytes | None = None
    timestamp: str | None = None
    nonce: str | None = None
    if rendered_binding.auth_scheme == "HMAC_SHA256":
        # FrozenExternalHttpBinding 已在构造时校验 HMAC 必须绑定 credential reference。
        credential_reference = cast("str", rendered_binding.credential_reference)
        secret = credential_provider.resolve(credential_reference)
        timestamp = now().isoformat()
        nonce = nonce_factory()
    dispatch = ExternalHttpDispatchRequest.from_persisted(
        binding=rendered_binding,
        canonical_payload_bytes=payload.body,
        payload_hash=payload.sha256,
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
    )
    return client.build_request(
        dispatch.method,
        dispatch.endpoint.url,
        params=dispatch.query_params,
        content=dispatch.body,
        headers=dispatch.headers,
    )


def _validate_business_reject(
    operation: WmsOperationDefinition,
    outcome: QueryBusinessReject | QueryTechnicalFailure | None,
) -> QueryBusinessReject | QueryTechnicalFailure | QueryContractFailure | None:
    if isinstance(outcome, QueryBusinessReject) and outcome.reason_code not in operation.reject_codes:
        return QueryContractFailure(
            reason_code="WMS_UNDECLARED_REJECT_CODE",
            message="WMS QUERY returned a reject code absent from the static Definition",
        )
    return outcome


class WmsRegistryQueryExecutor:
    """只解释静态 Definition，不识别任何具体 QUERY identity。"""

    def __init__(
        self,
        *,
        operation: WmsOperationDefinition,
        endpoint: CompiledWmsOperationEndpoint,
        frozen_binding: FrozenExternalHttpBinding,
        client: httpx.AsyncClient,
        credential_provider: WmsQueryCredentialProvider,
        evidence_writer: WmsRegistryQueryEvidenceWriter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] | None = None,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        if operation.mode is not WmsOperationMode.QUERY or endpoint.mode is not WmsOperationMode.QUERY:
            raise ValueError("registry QUERY executor requires QUERY semantics")
        if frozen_binding.operation_identity != operation.identity or endpoint.identity != operation.identity:
            raise ValueError("QUERY runtime binding identity mismatch")
        if (
            frozen_binding.target_snapshot.http_method != operation.http_method.value
            or endpoint.http_method is not operation.http_method
        ):
            raise ValueError("QUERY runtime binding method mismatch")
        if frozen_binding.target_snapshot.url != endpoint.endpoint_template:
            raise ValueError("QUERY frozen endpoint differs from compiled profile")
        self._operation = operation
        self._endpoint = endpoint
        self._frozen_binding = frozen_binding
        self._client = client
        self._credential_provider = credential_provider
        self._evidence_writer = evidence_writer
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory or (lambda: uuid4().hex)

    async def execute(self, request: BaseModel) -> WmsQueryOutcome[Any]:
        if not isinstance(request, self._operation.request_model):
            raise TypeError("QUERY executor requires its operation-specific typed request")
        projection = project_wms_query_request(
            operation=self._operation,
            endpoint=self._endpoint,
            request=request,
        )
        permit = await self._evidence_writer.before_call(
            operation_identity=self._operation.identity,
            target_code=self._operation.target_code,
        )
        state = _ExecutionBudgetState()
        if not permit.allowed:
            outcome: WmsQueryOutcome[Any] = QueryTechnicalFailure(
                reason_code="WMS_CIRCUIT_OPEN",
                message="WMS QUERY circuit breaker rejected the call",
                retryable=True,
                retry_after_seconds=permit.retry_after_seconds,
            )
            return await self._record(
                projection=projection,
                outcome=outcome,
                permit=permit,
                state=state,
            )

        outcome = await self._execute_attempts(
            request=request,
            state=state,
        )
        return await self._record(
            projection=projection,
            outcome=outcome,
            permit=permit,
            state=state,
        )

    async def _execute_attempts(
        self,
        *,
        request: BaseModel,
        state: _ExecutionBudgetState,
    ) -> WmsQueryOutcome[Any]:
        outcome: WmsQueryOutcome[Any] | None = None
        try:
            async with asyncio.timeout(self._operation.budget.deadline_seconds):
                for attempt_index in range(self._operation.budget.max_attempts):
                    state.attempt_count += 1
                    try:
                        outcome = await self._execute_once(request=request, state=state)
                    except httpx.TimeoutException:
                        outcome = QueryTechnicalFailure(
                            reason_code="WMS_PROVIDER_TIMEOUT",
                            message="WMS QUERY provider timed out",
                            retryable=True,
                        )
                    except httpx.ConnectError:
                        outcome = QueryTechnicalFailure(
                            reason_code="WMS_PROVIDER_UNAVAILABLE",
                            message="WMS QUERY provider connection failed",
                            retryable=True,
                        )
                    except httpx.RequestError:
                        outcome = QueryTechnicalFailure(
                            reason_code="WMS_PROVIDER_UNAVAILABLE",
                            message="WMS QUERY transport unavailable",
                            retryable=True,
                        )
                    except QueryBudgetViolation as exc:
                        outcome = QueryContractFailure(reason_code=exc.reason_code, message=exc.message)
                    except HttpChunkBudgetExceeded:
                        outcome = QueryContractFailure(
                            reason_code="WMS_CHUNK_BUDGET_EXCEEDED",
                            message="WMS QUERY response chunk exceeded the frozen budget",
                        )
                    except HttpWireBudgetExceeded:
                        outcome = QueryContractFailure(
                            reason_code="WMS_WIRE_BUDGET_EXCEEDED",
                            message="WMS QUERY response exceeded the frozen wire budget",
                        )
                    except HttpResponseContractError:
                        outcome = QueryContractFailure(
                            reason_code="WMS_MALFORMED_RESPONSE",
                            message="WMS QUERY response metadata is malformed",
                        )
                    except HttpContentEncodingFailure:
                        outcome = QueryContractFailure(
                            reason_code="WMS_CONTENT_ENCODING_INVALID",
                            message="WMS QUERY response content encoding is malformed",
                        )
                    except HttpUnsupportedContentEncoding:
                        outcome = QueryContractFailure(
                            reason_code="WMS_UNSUPPORTED_CONTENT_ENCODING",
                            message="WMS QUERY response used an unsupported content encoding",
                        )
                    except HttpDecodedBodyBudgetExceeded:
                        outcome = QueryContractFailure(
                            reason_code="WMS_DECODED_BUDGET_EXCEEDED",
                            message="decoded WMS QUERY response exceeded the frozen budget",
                        )
                    except HttpCompressionRatioExceeded:
                        outcome = QueryContractFailure(
                            reason_code="WMS_COMPRESSION_RATIO_EXCEEDED",
                            message="WMS QUERY response exceeded the frozen compression ratio",
                        )
                    except (
                        json.JSONDecodeError,
                        UnicodeDecodeError,
                        DecimalException,
                        ValidationError,
                        MalformedProviderResponse,
                    ):
                        outcome = QueryContractFailure(
                            reason_code="WMS_MALFORMED_RESPONSE",
                            message="WMS QUERY response violates its typed contract",
                        )
                    except (LookupError, TypeError, ValueError):
                        outcome = QueryContractFailure(
                            reason_code="WMS_QUERY_PREPARATION_FAILED",
                            message="WMS QUERY frozen binding or typed projection is invalid",
                        )
                    if not isinstance(outcome, QueryTechnicalFailure) or not outcome.retryable:
                        break
                    if attempt_index + 1 < self._operation.budget.max_attempts:
                        retry_after = outcome.retry_after_seconds or 0
                        await self._sleep(max(self._operation.budget.backoff_seconds[attempt_index], retry_after))
        except TimeoutError:
            outcome = QueryTechnicalFailure(
                reason_code="WMS_PROVIDER_TIMEOUT",
                message="WMS QUERY total deadline exceeded",
                retryable=True,
            )
        # WmsOperationBudget 已保证 max_attempts >= 1。
        return cast("WmsQueryOutcome[Any]", outcome)

    async def _execute_once(
        self,
        *,
        request: BaseModel,
        state: _ExecutionBudgetState,
    ) -> WmsQueryOutcome[Any]:
        if self._operation.pagination is None:
            projection = project_wms_query_request(
                operation=self._operation,
                endpoint=self._endpoint,
                request=request,
            )
            parsed = await self._send_and_parse(projection=projection, state=state)
            if isinstance(parsed, QueryBusinessReject | QueryTechnicalFailure | QueryContractFailure):
                return parsed
            result = validate_json_payload(self._operation.result_model, parsed)
            state.response_hash = _stable_hash(result.model_dump(mode="json", exclude_none=False))
            return QuerySuccess(result)
        return await self._execute_paginated(request=request, state=state)

    async def _execute_paginated(
        self,
        *,
        request: BaseModel,
        state: _ExecutionBudgetState,
    ) -> WmsQueryOutcome[Any]:
        # 仅由 `_execute_once` 的 pagination 分支进入。
        pagination = cast("WmsPaginationConstraint", self._operation.pagination)
        cursor = getattr(request, pagination.request_cursor_field, None)
        seen_cursors = {cursor} if cursor is not None else set()
        aggregate_items: list[Any] = []
        first_result: BaseModel | None = None
        source_version: object = None

        for _page_number in range(1, pagination.max_pages + 1):
            page_request = request.model_copy(update={pagination.request_cursor_field: cursor})
            projection = project_wms_query_request(
                operation=self._operation,
                endpoint=self._endpoint,
                request=page_request,
            )
            parsed = await self._send_and_parse(projection=projection, state=state)
            if isinstance(parsed, QueryBusinessReject | QueryTechnicalFailure | QueryContractFailure):
                return parsed
            page_result = validate_json_payload(self._operation.result_model, parsed)
            page_items = getattr(page_result, pagination.response_items_field)
            aggregate_items.extend(page_items)
            if len(aggregate_items) > pagination.max_rows:
                return QueryContractFailure(
                    reason_code="WMS_ROW_BUDGET_EXCEEDED",
                    message="WMS QUERY row budget exceeded",
                )
            page_source_version = getattr(page_result, "source_version", None)
            if first_result is None:
                first_result = page_result
                source_version = page_source_version
            elif page_source_version != source_version:
                return QueryContractFailure(
                    reason_code="WMS_SOURCE_VERSION_CHANGED_DURING_PAGINATION",
                    message="WMS QUERY source snapshot changed during pagination",
                )
            next_cursor = getattr(page_result, pagination.response_cursor_field)
            if next_cursor is None:
                payload = first_result.model_dump(mode="json", exclude_none=False)
                payload[pagination.response_items_field] = [
                    item.model_dump(mode="json", exclude_none=False) if isinstance(item, BaseModel) else item
                    for item in aggregate_items
                ]
                payload[pagination.response_cursor_field] = None
                result = validate_json_payload(self._operation.result_model, payload)
                state.response_hash = _stable_hash(result.model_dump(mode="json", exclude_none=False))
                return QuerySuccess(result)
            if next_cursor in seen_cursors:
                return QueryContractFailure(
                    reason_code="WMS_PAGINATION_CURSOR_REUSED",
                    message="WMS QUERY pagination cursor was reused",
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return QueryContractFailure(
            reason_code="WMS_PAGE_BUDGET_EXCEEDED",
            message="WMS QUERY page budget exceeded",
        )

    async def _send_and_parse(
        self,
        *,
        projection: WmsQueryRequestProjection,
        state: _ExecutionBudgetState,
    ) -> dict[str, Any] | QueryBusinessReject | QueryTechnicalFailure | QueryContractFailure:
        deadline = asyncio.get_running_loop().time() + self._operation.budget.deadline_seconds
        outbound_request = _build_http_request(
            client=self._client,
            projection=projection,
            frozen_binding=self._frozen_binding,
            credential_provider=self._credential_provider,
            now=self._now,
            nonce_factory=self._nonce_factory,
        )
        response, state.cumulative_wire_bytes = await send_bounded_wms_request(
            client=self._client,
            request=outbound_request,
            authenticate=lambda _request: None,
            deadline=deadline,
            max_chunk_bytes=self._operation.budget.max_chunk_bytes,
            max_wire_bytes=self._operation.budget.max_wire_bytes,
            cumulative_wire_bytes=state.cumulative_wire_bytes,
        )
        state.http_status = response.status_code
        failure = _validate_business_reject(
            self._operation,
            classify_http_failure(response.status_code, None, response.headers),
        )
        if failure is not None and (
            response.status_code in {401, 403, 408, 429} or not (400 <= response.status_code < 500)
        ):
            return failure
        if 400 <= response.status_code < 500:
            failure_payload = parse_optional_failure_body(
                response.body,
                content_encoding=response.headers.get("content-encoding", "identity"),
                budget=self._operation.budget,
            )
            classified = _validate_business_reject(
                self._operation,
                classify_http_failure(response.status_code, failure_payload, response.headers),
            )
            return classified or QueryTechnicalFailure(
                reason_code="WMS_PROVIDER_CLIENT_ERROR",
                message="WMS QUERY provider returned a client error",
                retryable=False,
            )
        decoded = decode_bounded_http_body(
            response.body,
            content_encoding=response.headers.get("content-encoding", "identity"),
            allowed_content_encodings=self._operation.budget.allowed_content_encodings,
            max_decoded_bytes=self._operation.budget.max_decoded_bytes - state.cumulative_decoded_bytes,
            max_compression_ratio=self._operation.budget.max_compression_ratio,
        )
        state.cumulative_decoded_bytes += len(decoded)
        parsed = parse_bounded_json(
            decoded,
            max_depth=self._operation.budget.max_json_depth,
            max_field_length=self._operation.budget.max_field_length,
        )
        if not isinstance(parsed, dict):
            raise MalformedProviderResponse("successful QUERY response must be a JSON object")
        return parsed

    async def _record(
        self,
        *,
        projection: WmsQueryRequestProjection,
        outcome: WmsQueryOutcome[Any],
        permit: WmsQueryCallPermit,
        state: _ExecutionBudgetState,
    ) -> WmsQueryOutcome[Any]:
        try:
            record = await self._evidence_writer.record(
                operation_identity=self._operation.identity,
                target_code=self._operation.target_code,
                profile_identity=self._frozen_binding.provider_profile_identity,
                profile_digest=self._frozen_binding.provider_profile_hash,
                endpoint_digest=self._endpoint.endpoint_digest,
                request_snapshot=projection.evidence_snapshot,
                request_canonical_hash=projection.request_canonical_hash,
                response_hash=state.response_hash,
                attempt_count=state.attempt_count,
                http_status=state.http_status,
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
        evidence_key = record.evidence_key
        if not isinstance(evidence_key, str) or not evidence_key.strip():
            return QueryContractFailure(
                reason_code="WMS_EVIDENCE_WRITE_FAILED",
                message="WMS QUERY evidence writer returned no key",
            )
        return replace(record.outcome, evidence_key=evidence_key)


__all__ = [
    "WmsQueryCredentialProvider",
    "WmsRegistryQueryEvidenceWriter",
    "WmsRegistryQueryExecutor",
]
