"""WMS EFFECT 状态查询 HTTP adapter。"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from src.app.wms_integration.ports.effect_status import (
    FrozenWmsEffectStatusBinding,
    WmsEffectStatusRequest,
    WmsEffectStatusSnapshot,
    parse_wms_effect_status_snapshot,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
    WmsQueryOutcome,
)
from src.app.wms_integration.services.http_transport import (
    WmsHttpResponseContractError,
    WmsHttpWireBudgetExceeded,
    open_wms_http_client,
    send_bounded_wms_request,
    sign_wms_hmac_request,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.app.sys.external_http_credentials import VersionedCredentialProvider
    from src.app.wms_integration.services.query_transport import WmsQueryCallPermit, WmsQueryEvidenceWriter

_BACKOFF_RANDOM = random.SystemRandom()
_RETRY_AFTER_DELTA_SECONDS_RE = re.compile(r"^[0-9]+$", re.ASCII)
WMS_EFFECT_STATUS_TARGET_CODE = "WMS_EFFECT_STATUS"


class WmsEffectStatusQueryError(RuntimeError):
    """把 transport/合同错误收敛到既有 QUERY 命名分类。"""

    def __init__(self, failure: QueryTechnicalFailure | QueryContractFailure) -> None:
        self.failure = failure
        super().__init__(failure.message)


def _retry_after_seconds(
    value: str | None,
    *,
    now: datetime,
) -> float | None:
    if value is None:
        return None
    if _RETRY_AFTER_DELTA_SECONDS_RE.fullmatch(value):
        try:
            seconds = float(value)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(seconds):
            return None
        return seconds
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if target is None or target.tzinfo is None:
        return None
    return max(0.0, (target.astimezone(UTC) - now.astimezone(UTC)).total_seconds())


class WmsEffectStatusQueryAdapter:
    """只使用 Intent frozen status binding 执行可重复的无副作用查询。"""

    def __init__(
        self,
        *,
        binding: FrozenWmsEffectStatusBinding,
        credential_provider: VersionedCredentialProvider,
        evidence_writer: WmsQueryEvidenceWriter,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
        nonce_factory: Callable[[], str] | None = None,
        jitter: Callable[[float], float] | None = None,
        initial_backoff_seconds: float,
        max_backoff_seconds: float,
    ) -> None:
        if initial_backoff_seconds <= 0 or max_backoff_seconds < initial_backoff_seconds:
            raise ValueError("status backoff bounds are invalid")
        self._binding = binding
        self._credential_provider = credential_provider
        self._evidence_writer = evidence_writer
        self._transport = transport
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory or (lambda: uuid4().hex)
        self._jitter = jitter or (lambda upper: _BACKOFF_RANDOM.uniform(0.0, upper))
        self._initial_backoff_seconds = initial_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds

    async def query_status(self, request: WmsEffectStatusRequest) -> WmsEffectStatusSnapshot:
        try:
            permit = await self._evidence_writer.before_call(
                operation_identity=request.operation_identity,
                target_code=WMS_EFFECT_STATUS_TARGET_CODE,
            )
        except DBAPIError:
            raise
        except Exception as exc:
            raise WmsEffectStatusQueryError(
                QueryContractFailure(
                    reason_code="WMS_EVIDENCE_WRITE_FAILED",
                    message="WMS EFFECT status breaker gate could not be persisted",
                )
            ) from exc

        if not permit.allowed:
            outcome: WmsQueryOutcome[WmsEffectStatusSnapshot] = QueryTechnicalFailure(
                reason_code="WMS_CIRCUIT_OPEN",
                message="WMS EFFECT status circuit breaker rejected the call",
                retryable=True,
                retry_after_seconds=permit.retry_after_seconds,
            )
        else:
            outcome = await self._query_status_outcome(request)
        recorded = await self._record_outcome(
            request=request,
            outcome=outcome,
            permit=permit,
        )
        if isinstance(recorded, QuerySuccess):
            return recorded.value
        raise WmsEffectStatusQueryError(recorded)

    async def _query_status_outcome(
        self,
        request: WmsEffectStatusRequest,
    ) -> WmsQueryOutcome[WmsEffectStatusSnapshot]:
        try:
            secret = self._credential_provider.resolve(self._binding.credential_reference)
        except Exception:
            secret = None
        if not isinstance(secret, bytes) or not secret:
            return QueryContractFailure(
                reason_code="WMS_CREDENTIAL_UNAVAILABLE",
                message="frozen WMS status credential revision is unavailable",
            )

        deadline = asyncio.get_running_loop().time() + self._binding.target.timeout_seconds
        try:
            async with open_wms_http_client(
                transport=self._transport,
                timeout_seconds=self._binding.target.timeout_seconds,
                deadline=deadline,
            ) as client:
                # operation-specific adapter 只构建请求；
                # 签名、发送、预算和关闭统一委托给共享 transport。
                outbound = client.build_request(
                    self._binding.target.http_method,
                    self._binding.target.url,
                    params=request.query_params,
                )
                response, _ = await send_bounded_wms_request(
                    client=client,
                    request=outbound,
                    authenticate=lambda authenticated_request: sign_wms_hmac_request(
                        authenticated_request,
                        credential_reference=self._binding.credential_reference,
                        auth_scheme=self._binding.auth_scheme,
                        secret=secret,
                        now=self._now,
                        nonce_factory=self._nonce_factory,
                    ),
                    deadline=deadline,
                    max_chunk_bytes=self._binding.target.max_response_bytes,
                    max_wire_bytes=self._binding.target.max_response_bytes,
                )
        except (TimeoutError, httpx.TimeoutException):
            return QueryTechnicalFailure(
                reason_code="WMS_PROVIDER_TIMEOUT",
                message="WMS EFFECT status query timed out",
                retryable=True,
                retry_after_seconds=self._local_backoff(request.attempt_count),
            )
        except WmsHttpWireBudgetExceeded:
            return QueryContractFailure(
                reason_code="WMS_STATUS_RESPONSE_TOO_LARGE",
                message="WMS EFFECT status response exceeds the frozen response budget",
            )
        except WmsHttpResponseContractError:
            return QueryContractFailure(
                reason_code="WMS_STATUS_CONTRACT_INVALID",
                message="WMS EFFECT status response metadata violates the typed contract",
            )
        except (httpx.HTTPError, RuntimeError, ValueError):
            return QueryTechnicalFailure(
                reason_code="WMS_PROVIDER_UNAVAILABLE",
                message="WMS EFFECT status transport failed",
                retryable=True,
                retry_after_seconds=self._local_backoff(request.attempt_count),
            )

        failure = self._classify_http_failure(
            status_code=response.status_code,
            retry_after=response.headers.get("Retry-After"),
            attempt_count=request.attempt_count,
        )
        if failure is not None:
            return failure
        try:
            decoded = json.loads(response.body)
            return QuerySuccess(
                parse_wms_effect_status_snapshot(
                    request=request,
                    raw_response=decoded,
                    max_result_payload_bytes=self._binding.target.max_response_bytes,
                )
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError):
            return QueryContractFailure(
                reason_code="WMS_STATUS_CONTRACT_INVALID",
                message="WMS EFFECT status response violates the typed contract",
            )

    async def _record_outcome(
        self,
        *,
        request: WmsEffectStatusRequest,
        outcome: WmsQueryOutcome[WmsEffectStatusSnapshot],
        permit: WmsQueryCallPermit,
    ) -> WmsQueryOutcome[WmsEffectStatusSnapshot]:
        try:
            evidence_key = await self._evidence_writer.record(
                operation_identity=request.operation_identity,
                target_code=WMS_EFFECT_STATUS_TARGET_CODE,
                request_snapshot=request.model_dump(mode="json", exclude_none=True),
                outcome=outcome,
                permit=permit,
            )
        except DBAPIError:
            raise
        except Exception:
            return QueryContractFailure(
                reason_code="WMS_EVIDENCE_WRITE_FAILED",
                message="WMS EFFECT status evidence could not be persisted",
            )
        if not isinstance(evidence_key, str) or not evidence_key.strip():
            return QueryContractFailure(
                reason_code="WMS_EVIDENCE_WRITE_FAILED",
                message="WMS EFFECT status evidence writer returned no key",
            )
        return replace(outcome, evidence_key=evidence_key)

    def _classify_http_failure(
        self,
        *,
        status_code: int,
        retry_after: str | None,
        attempt_count: int,
    ) -> QueryTechnicalFailure | QueryContractFailure | None:
        if 200 <= status_code < 300:
            return None
        if status_code in {401, 403}:
            return QueryContractFailure(
                reason_code="WMS_AUTH_REJECTED",
                message="WMS EFFECT status authentication was rejected",
            )
        if status_code == 429:
            local_delay = self._local_backoff(attempt_count)
            provider_delay = _retry_after_seconds(
                retry_after,
                now=self._now(),
            )
            return QueryTechnicalFailure(
                reason_code="WMS_RATE_LIMITED",
                message="WMS EFFECT status query was rate limited",
                retryable=True,
                retry_after_seconds=max(local_delay, provider_delay) if provider_delay is not None else local_delay,
            )
        if status_code >= 500:
            return QueryTechnicalFailure(
                reason_code="WMS_PROVIDER_UNAVAILABLE",
                message="WMS EFFECT status provider is unavailable",
                retryable=True,
                retry_after_seconds=self._local_backoff(attempt_count),
            )
        return QueryContractFailure(
            reason_code="WMS_STATUS_HTTP_ERROR",
            message="WMS EFFECT status endpoint returned an unsupported client error",
        )

    def _local_backoff(self, attempt_count: int) -> float:
        exponent = max(0, attempt_count - 1)
        saturation_exponent = (
            math.ceil(math.log2(self._max_backoff_seconds / self._initial_backoff_seconds))
            if self._max_backoff_seconds > self._initial_backoff_seconds
            else 0
        )
        base = (
            self._max_backoff_seconds
            if exponent >= saturation_exponent
            else min(self._max_backoff_seconds, self._initial_backoff_seconds * (2**exponent))
        )
        jitter_window = base / 2
        jitter = max(0.0, min(jitter_window, float(self._jitter(jitter_window))))
        return base - jitter


__all__ = [
    "WMS_EFFECT_STATUS_TARGET_CODE",
    "WmsEffectStatusQueryAdapter",
    "WmsEffectStatusQueryError",
]
