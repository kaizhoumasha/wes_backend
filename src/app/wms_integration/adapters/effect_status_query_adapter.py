"""WMS EFFECT 状态查询 HTTP adapter。"""

from __future__ import annotations

import asyncio
import json
import math
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from pydantic import ValidationError

from src.app.wms_integration.ports.effect_status import (
    FrozenWmsEffectStatusBinding,
    WmsEffectStatusRequest,
    WmsEffectStatusSnapshot,
    parse_wms_effect_status_snapshot,
)
from src.app.wms_integration.ports.query_outcome import QueryContractFailure, QueryTechnicalFailure
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

_BACKOFF_RANDOM = random.SystemRandom()


class WmsEffectStatusQueryError(RuntimeError):
    """把 transport/合同错误收敛到既有 QUERY 命名分类。"""

    def __init__(self, failure: QueryTechnicalFailure | QueryContractFailure) -> None:
        self.failure = failure
        super().__init__(failure.message)


def _retry_after_seconds(value: str | None, *, now: datetime) -> float | None:
    if value is None:
        return None
    normalized = value.strip()
    try:
        seconds = int(normalized)
    except ValueError:
        try:
            target = parsedate_to_datetime(normalized)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            return None
        return max(0.0, (target.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
    if seconds < 0:
        return None
    try:
        delay = float(seconds)
    except OverflowError:
        return None
    return delay if math.isfinite(delay) else None


class WmsEffectStatusQueryAdapter:
    """只使用 Intent frozen status binding 执行可重复的无副作用查询。"""

    def __init__(
        self,
        *,
        binding: FrozenWmsEffectStatusBinding,
        credential_provider: VersionedCredentialProvider,
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
        self._transport = transport
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory or (lambda: uuid4().hex)
        self._jitter = jitter or (lambda upper: _BACKOFF_RANDOM.uniform(0.0, upper))
        self._initial_backoff_seconds = initial_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds

    async def query_status(self, request: WmsEffectStatusRequest) -> WmsEffectStatusSnapshot:
        try:
            secret = self._credential_provider.resolve(self._binding.credential_reference)
        except Exception as exc:
            raise WmsEffectStatusQueryError(
                QueryContractFailure(
                    reason_code="WMS_CREDENTIAL_UNAVAILABLE",
                    message="frozen WMS status credential revision is unavailable",
                )
            ) from exc
        if not isinstance(secret, bytes) or not secret:
            raise WmsEffectStatusQueryError(
                QueryContractFailure(
                    reason_code="WMS_CREDENTIAL_UNAVAILABLE",
                    message="frozen WMS status credential revision is unavailable",
                )
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
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise WmsEffectStatusQueryError(
                QueryTechnicalFailure(
                    reason_code="WMS_PROVIDER_TIMEOUT",
                    message="WMS EFFECT status query timed out",
                    retryable=True,
                    retry_after_seconds=self._local_backoff(request.attempt_count),
                )
            ) from exc
        except WmsHttpWireBudgetExceeded as exc:
            raise WmsEffectStatusQueryError(
                QueryContractFailure(
                    reason_code="WMS_STATUS_RESPONSE_TOO_LARGE",
                    message="WMS EFFECT status response exceeds the frozen response budget",
                )
            ) from exc
        except WmsHttpResponseContractError as exc:
            raise WmsEffectStatusQueryError(
                QueryContractFailure(
                    reason_code="WMS_STATUS_CONTRACT_INVALID",
                    message="WMS EFFECT status response metadata violates the typed contract",
                )
            ) from exc
        except WmsEffectStatusQueryError:
            raise
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise WmsEffectStatusQueryError(
                QueryTechnicalFailure(
                    reason_code="WMS_PROVIDER_UNAVAILABLE",
                    message="WMS EFFECT status transport failed",
                    retryable=True,
                    retry_after_seconds=self._local_backoff(request.attempt_count),
                )
            ) from exc

        failure = self._classify_http_failure(
            status_code=response.status_code,
            retry_after=response.headers.get("Retry-After"),
            attempt_count=request.attempt_count,
        )
        if failure is not None:
            raise WmsEffectStatusQueryError(failure)
        try:
            decoded = json.loads(response.body)
            return parse_wms_effect_status_snapshot(
                request=request,
                raw_response=decoded,
                max_result_payload_bytes=self._binding.target.max_response_bytes,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise WmsEffectStatusQueryError(
                QueryContractFailure(
                    reason_code="WMS_STATUS_CONTRACT_INVALID",
                    message="WMS EFFECT status response violates the typed contract",
                )
            ) from exc

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
            provider_delay = _retry_after_seconds(retry_after, now=self._now())
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
        base = min(
            self._max_backoff_seconds,
            self._initial_backoff_seconds * (2 ** (attempt_count - 1)),
        )
        jitter = max(0.0, min(base, float(self._jitter(base))))
        return min(self._max_backoff_seconds, base + jitter)


__all__ = ["WmsEffectStatusQueryAdapter", "WmsEffectStatusQueryError"]
