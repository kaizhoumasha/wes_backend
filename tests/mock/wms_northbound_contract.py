"""WMS 北向 Mock 的认证、幂等、typed validator 与状态机核心。

Mock 镜像只复制静态 operation registry 与 typed ports，不导入 WES runtime。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
from src.app.wms_integration.ports.operation_common import validate_json_payload
from tests.mock.wms_operation_fixtures import RESULT_FIXTURES

if TYPE_CHECKING:
    from collections.abc import Callable

MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V1 = "secret://wms/material-flow-sandbox-hmac@v1"
MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V2 = "secret://wms/material-flow-sandbox-hmac@v2"
ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE = MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V2
MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1 = "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1"
MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2 = "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2"
_CREDENTIAL_ENV_BY_REFERENCE = {
    MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V1: MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1,
    MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V2: MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2,
}
_KNOWN_OPERATION_IDENTITIES = frozenset(
    identity for identity, operation in WMS_OPERATION_BY_IDENTITY.items() if operation.mode is WmsOperationMode.EFFECT
)
_ASYNC_OPERATION_IDENTITIES = frozenset(
    identity
    for identity, operation in WMS_OPERATION_BY_IDENTITY.items()
    if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
)
_ALLOWED_REJECTION_REASON_CODES_BY_OPERATION = {
    identity: frozenset(operation.reject_codes)
    for identity, operation in WMS_OPERATION_BY_IDENTITY.items()
    if operation.mode is WmsOperationMode.EFFECT
}
_HMAC_CLOCK_SKEW_SECONDS = 30
_HMAC_NONCE_TTL_SECONDS = 300


class NorthboundAuthError(ValueError):
    """北向 Mock 认证失败，``code`` 可直接映射为稳定 HTTP 错误码。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NorthboundPayloadValidationError(ValueError):
    """typed wire body 不满足冻结 required/allowed/type 合同。"""


class NorthboundHmacReplayGuard:
    """以真实时钟校验签名新鲜度，并原子消费短期 nonce。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        clock_skew_seconds: int = _HMAC_CLOCK_SKEW_SECONDS,
        nonce_ttl_seconds: int = _HMAC_NONCE_TTL_SECONDS,
    ) -> None:
        if clock_skew_seconds < 0 or nonce_ttl_seconds <= 0:
            raise ValueError("HMAC clock skew and nonce TTL must be valid")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._clock_skew_seconds = clock_skew_seconds
        self._nonce_ttl_seconds = nonce_ttl_seconds
        self._lock = RLock()
        self._seen: dict[tuple[str, str], int] = {}

    def consume(
        self,
        *,
        credential_reference: str,
        timestamp: str,
        nonce: str,
    ) -> None:
        """验签成功后消费 nonce；过期、未来或重复请求均 fail closed。"""

        current = self._current_timestamp()
        signed_at = self._parse_signed_timestamp(timestamp)
        if abs(current - signed_at) > self._clock_skew_seconds:
            raise NorthboundAuthError("SIGNATURE_TIMESTAMP_OUT_OF_WINDOW")
        key = (credential_reference, nonce)
        with self._lock:
            self._prune(current)
            expires_at = self._seen.get(key)
            if expires_at is not None and expires_at > current:
                raise NorthboundAuthError("HMAC_NONCE_REPLAYED")
            self._seen[key] = current + self._nonce_ttl_seconds

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()

    def _current_timestamp(self) -> int:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("Northbound HMAC clock must be timezone-aware")
        return int(current.timestamp())

    def _parse_signed_timestamp(self, value: str) -> int:
        if value.isdecimal():
            return int(value)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise NorthboundAuthError("INVALID_SIGNATURE_TIMESTAMP") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise NorthboundAuthError("INVALID_SIGNATURE_TIMESTAMP")
        return int(parsed.timestamp())

    def _prune(self, current: int) -> None:
        expired = [key for key, expires_at in self._seen.items() if expires_at <= current]
        for key in expired:
            self._seen.pop(key, None)


def content_sha256(body: bytes) -> str:
    """返回实际 wire bytes 的小写 SHA-256 指纹。"""

    return hashlib.sha256(body).hexdigest()


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    """按 WES frozen payload 规则生成唯一 JSON object bytes。"""

    if not isinstance(payload, Mapping):
        raise NorthboundPayloadValidationError("typed request must be a JSON object")
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NorthboundPayloadValidationError("typed request must contain valid JSON values") from exc


def resolve_mock_northbound_credential(credential_reference: str) -> bytes:
    """解析真实 WES sandbox material-flow active/frozen credential reference。"""

    env_name = _CREDENTIAL_ENV_BY_REFERENCE.get(credential_reference)
    if env_name is None:
        raise NorthboundAuthError("CREDENTIAL_REFERENCE_REJECTED")
    secret = os.getenv(env_name, "")
    if not secret:
        raise NorthboundAuthError("CREDENTIAL_UNAVAILABLE")
    return secret.encode("utf-8")


def canonical_submit_string(
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    payload_hash: str,
    operation_identity: str,
    idempotency_key: str,
) -> str:
    """按冻结 Submit 七项顺序构造 HMAC canonical input。"""

    return "\n".join((method, path or "/", timestamp, nonce, payload_hash, operation_identity, idempotency_key))


def canonical_status_string(*, method: str, path: str, timestamp: str, nonce: str, payload_hash: str) -> str:
    """按冻结 Status 五项顺序构造 HMAC canonical input。"""

    return "\n".join((method, path or "/", timestamp, nonce, payload_hash))


def verify_submit_hmac(headers: Mapping[str, str], body: bytes, *, method: str, path: str) -> None:
    """验证 Submit header、内容指纹和七项 HMAC；成功时不返回业务数据。"""

    normalized = _normalized_headers(headers)
    payload_hash = _verify_content_hash(normalized, "x-wes-content-sha256", body)
    _require_algorithm(normalized, "x-wes-signature-algorithm")
    credential_reference = _required_header(normalized, "x-wes-credential-reference")
    timestamp = _required_header(normalized, "x-wes-timestamp")
    nonce = _required_header(normalized, "x-wes-nonce")
    operation_identity = _required_header(normalized, "x-wes-operation-identity")
    idempotency_key = _required_header(normalized, "idempotency-key")
    expected = _signature(
        resolve_mock_northbound_credential(credential_reference),
        canonical_submit_string(
            method=method,
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            payload_hash=payload_hash,
            operation_identity=operation_identity,
            idempotency_key=idempotency_key,
        ),
    )
    _verify_signature(normalized, "x-wes-signature", expected)


def verify_status_hmac(headers: Mapping[str, str], body: bytes, *, method: str, path: str) -> None:
    """验证 Status header 与收到的 raw request target 的五项 HMAC。"""

    if method == "GET" and body:
        raise NorthboundAuthError("CONTENT_HASH_MISMATCH")
    normalized = _normalized_headers(headers)
    payload_hash = _verify_content_hash(normalized, "x-wms-content-sha256", body)
    _require_algorithm(normalized, "x-wms-signature-algorithm")
    credential_reference = _required_header(normalized, "x-wms-credential-reference")
    timestamp = _required_header(normalized, "x-wms-timestamp")
    nonce = _required_header(normalized, "x-wms-nonce")
    expected = _signature(
        resolve_mock_northbound_credential(credential_reference),
        canonical_status_string(method=method, path=path, timestamp=timestamp, nonce=nonce, payload_hash=payload_hash),
    )
    _verify_signature(normalized, "x-wms-signature", expected)


def validate_typed_request(operation_identity: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """在幂等写入前按静态 Definition 校验 operation-specific wire schema。"""

    try:
        operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    except KeyError as exc:
        raise NorthboundPayloadValidationError("unsupported operation_identity") from exc
    if operation.mode is not WmsOperationMode.EFFECT:
        raise NorthboundPayloadValidationError("northbound submit only accepts EFFECT operation")
    try:
        validated = validate_json_payload(operation.request_model, payload)
    except ValidationError as exc:
        raise NorthboundPayloadValidationError("typed request fields are invalid") from exc
    return validated.model_dump(mode="json", exclude_none=True)


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in headers.items()}


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name, "")
    if not value or value != value.strip() or "\r" in value or "\n" in value:
        raise NorthboundAuthError("MISSING_OR_INVALID_AUTH_HEADER")
    return value


def _require_algorithm(headers: Mapping[str, str], name: str) -> None:
    if headers.get(name) != "HMAC_SHA256":
        raise NorthboundAuthError("INVALID_SIGNATURE_ALGORITHM")


def _verify_content_hash(headers: Mapping[str, str], name: str, body: bytes) -> str:
    supplied_hash = _required_header(headers, name)
    if not hmac.compare_digest(supplied_hash, content_sha256(body)):
        raise NorthboundAuthError("CONTENT_HASH_MISMATCH")
    return supplied_hash


def _signature(secret: bytes, canonical: str) -> str:
    return hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_signature(headers: Mapping[str, str], name: str, expected: str) -> None:
    if not hmac.compare_digest(expected, headers.get(name, "")):
        raise NorthboundAuthError("INVALID_HMAC_SIGNATURE")


@dataclass(frozen=True, slots=True)
class NorthboundStatusSnapshot:
    """北向 status wire schema 的不可变快照。"""

    state: str
    provider_reference: str | None
    reason_code: str | None
    updated_at: str | None
    source_version: int | None
    result_payload: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "provider_reference": self.provider_reference,
            "reason_code": self.reason_code,
            "updated_at": self.updated_at,
            "source_version": self.source_version,
            "result_payload": self.result_payload,
        }


@dataclass(frozen=True, slots=True)
class NorthboundSubmission:
    """Submit 的最小决定，HTTP route 可据此保持稳定响应形状。"""

    status_code: int
    snapshot: NorthboundStatusSnapshot | None = None
    error_code: str | None = None


@dataclass(slots=True)
class _OperationRecord:
    fingerprint: str
    payload: dict[str, Any]
    provider_reference: str
    accepted_at: datetime
    visible_at: datetime
    expires_at: datetime
    state: str
    source_version: int
    updated_at: str
    reason_code: str | None = None
    result_payload: dict[str, Any] | None = None
    callback_hint_registered: bool = False
    effect_count: int = 1


class NorthboundOperationStore:
    """由单一锁保护的 Mock 北向幂等记录与确定性状态推进。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        retention_seconds: int = 9,
        visibility_sla_seconds: float = 2,
    ) -> None:
        if retention_seconds <= 0 or not math.isfinite(visibility_sla_seconds) or visibility_sla_seconds <= 0:
            raise ValueError("northbound retention and visibility SLA must be positive")
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retention_seconds = retention_seconds
        self._visibility_sla_seconds = visibility_sla_seconds
        self._records: dict[tuple[str, str], _OperationRecord] = {}
        self._pending_visibility_delays: dict[tuple[str, str], float] = {}
        self._effect_counts: dict[tuple[str, str], int] = {}

    def submit(
        self,
        operation_identity: str,
        idempotency_key: str,
        fingerprint: str,
        payload: Mapping[str, Any],
    ) -> NorthboundSubmission:
        """首次受理，同键重放或冲突均在同一临界区内判定。"""

        key = self._record_key(operation_identity, idempotency_key)
        with self._lock:
            current = self._current_time()
            record = self._active_record(key, current)
            if record is None:
                visibility_delay = self._pending_visibility_delays.pop(key, 0)
                effect_count = self._effect_counts.get(key, 0) + 1
                self._effect_counts[key] = effect_count
                if operation_identity in _ASYNC_OPERATION_IDENTITIES:
                    WmsEffectAck.model_validate(
                        build_typed_ack(
                            operation_identity,
                            idempotency_key,
                            payload,
                            submission_state="ACCEPTED",
                        )
                    )
                record = _OperationRecord(
                    fingerprint=fingerprint,
                    payload=dict(payload),
                    provider_reference=self._provider_reference(operation_identity, idempotency_key),
                    accepted_at=current,
                    visible_at=current + timedelta(seconds=visibility_delay),
                    expires_at=current + timedelta(seconds=self._retention_seconds),
                    state="ACCEPTED",
                    source_version=0,
                    updated_at=current.isoformat(),
                    effect_count=effect_count,
                )
                self._records[key] = record
                if operation_identity not in _ASYNC_OPERATION_IDENTITIES:
                    self._transition(record, "COMPLETED", operation_identity=operation_identity)
                    return NorthboundSubmission(status_code=200, snapshot=self._snapshot(record))
                return NorthboundSubmission(status_code=202, snapshot=self._snapshot(record))
            if not hmac.compare_digest(record.fingerprint, fingerprint):
                return NorthboundSubmission(status_code=422, error_code="IDEMPOTENCY_CONFLICT")
            snapshot = self._snapshot(record)
            if record.state == "COMPLETED":
                return NorthboundSubmission(status_code=200, snapshot=snapshot)
            if record.state == "REJECTED":
                return NorthboundSubmission(status_code=200, snapshot=snapshot)
            return NorthboundSubmission(
                status_code=409, snapshot=snapshot, error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS"
            )

    def query(self, operation_identity: str, idempotency_key: str) -> NorthboundStatusSnapshot:
        """按时钟判定不可见/过期；可见后再推进 ACCEPTED→PROCESSING→COMPLETED。"""

        if operation_identity not in _ASYNC_OPERATION_IDENTITIES:
            raise ValueError("status query only accepts ASYNC_TASK operation")
        with self._lock:
            key = self._record_key(operation_identity, idempotency_key)
            current = self._current_time()
            record = self._active_record(key, current)
            if record is None:
                return _not_found_snapshot()
            if current < record.visible_at:
                return _not_found_snapshot()
            snapshot = self._snapshot(record)
            if record.state == "ACCEPTED":
                self._transition(record, "PROCESSING")
            elif record.state == "PROCESSING":
                self._transition(record, "COMPLETED", operation_identity=operation_identity)
            return snapshot

    def reject(self, operation_identity: str, idempotency_key: str, *, reason_code: str) -> NorthboundStatusSnapshot:
        """将已受理记录置为拒绝终态，供 Mock 故障场景使用。"""

        if reason_code not in _ALLOWED_REJECTION_REASON_CODES_BY_OPERATION[operation_identity]:
            raise ValueError("reason_code is not allowed for operation_identity")
        with self._lock:
            key = self._record_key(operation_identity, idempotency_key)
            record = self._active_record(key, self._current_time())
            if record is None:
                return _not_found_snapshot()
            if record.state not in {"COMPLETED", "REJECTED"}:
                self._transition(record, "REJECTED", reason_code=reason_code)
            return self._snapshot(record)

    def register_callback_hint(self, operation_identity: str, idempotency_key: str) -> bool:
        """只允许现存 record 首次登记 callback hint，避免重复触发状态查询。"""

        if operation_identity not in _ASYNC_OPERATION_IDENTITIES:
            return False
        with self._lock:
            key = self._record_key(operation_identity, idempotency_key)
            record = self._active_record(key, self._current_time())
            if record is None or record.callback_hint_registered:
                return False
            record.callback_hint_registered = True
            return True

    def effect_count(self, operation_identity: str, idempotency_key: str) -> int:
        with self._lock:
            return self._effect_counts.get(self._record_key(operation_identity, idempotency_key), 0)

    def configure_visibility_delay(
        self,
        operation_identity: str,
        idempotency_key: str,
        *,
        delay_seconds: float,
    ) -> None:
        """Mock-only 控制面按秒设置 ``visible_at``，不得超过公开 SLA。"""

        if not 0 <= delay_seconds <= self._visibility_sla_seconds:
            raise ValueError("visibility delay must be within the declared SLA")
        key = self._record_key(operation_identity, idempotency_key)
        with self._lock:
            record = self._active_record(key, self._current_time())
            if record is None:
                self._pending_visibility_delays[key] = delay_seconds
            else:
                record.visible_at = record.accepted_at + timedelta(seconds=delay_seconds)

    def reset(self) -> None:
        """清理全部北向记录和 callback hint 登记，供 /debug/reset 复位。"""

        with self._lock:
            self._records.clear()
            self._pending_visibility_delays.clear()
            self._effect_counts.clear()

    def _transition(
        self,
        record: _OperationRecord,
        state: str,
        *,
        operation_identity: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        record.state = state
        record.source_version += 1
        record.updated_at = self._now()
        record.reason_code = reason_code
        if state == "COMPLETED":
            if operation_identity is None:
                raise ValueError("operation_identity is required for completed result")
            record.result_payload = build_typed_result(
                operation_identity,
                record.payload,
                source_version=record.source_version,
                completed_at=record.updated_at,
                provider_reference=record.provider_reference,
            )
        else:
            record.result_payload = None

    def _snapshot(self, record: _OperationRecord) -> NorthboundStatusSnapshot:
        return NorthboundStatusSnapshot(
            state=record.state,
            provider_reference=record.provider_reference,
            reason_code=record.reason_code,
            updated_at=record.updated_at,
            source_version=record.source_version,
            result_payload=dict(record.result_payload) if record.result_payload is not None else None,
        )

    def _record_key(self, operation_identity: str, idempotency_key: str) -> tuple[str, str]:
        if operation_identity not in _KNOWN_OPERATION_IDENTITIES:
            raise ValueError("unsupported operation_identity")
        if not idempotency_key:
            raise ValueError("idempotency_key must be non-empty")
        return operation_identity, idempotency_key

    def _provider_reference(self, operation_identity: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{operation_identity}\n{idempotency_key}".encode()).hexdigest()[:16]
        return f"mock-wms:{digest}"

    def _active_record(
        self,
        key: tuple[str, str],
        current: datetime,
    ) -> _OperationRecord | None:
        record = self._records.get(key)
        if record is not None and current >= record.expires_at:
            self._records.pop(key, None)
            return None
        return record

    def _current_time(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("Northbound mock clock must be timezone-aware")
        return current.astimezone(UTC)

    def _now(self) -> str:
        return self._current_time().isoformat()


def build_typed_result(
    operation_identity: str,
    payload: Mapping[str, Any],
    *,
    source_version: int,
    completed_at: str,
    provider_reference: str | None = None,
) -> dict[str, Any]:
    """从 29 项 fixture 清单构造与请求关联的 typed terminal result。"""

    try:
        result = deepcopy(RESULT_FIXTURES[operation_identity])
        operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    except KeyError as exc:
        raise ValueError("unsupported operation_identity") from exc
    del completed_at
    for field_name in operation.result_model.model_fields:
        if field_name in payload and not isinstance(payload[field_name], (dict, list)):
            result[field_name] = payload[field_name]
    if "source_version" in result:
        result["source_version"] = str(source_version)
    if "provider_reference" in result:
        result["provider_reference"] = provider_reference or f"mock:{payload.get('dispatch_key', 'query')}"
    return validate_json_payload(operation.result_model, result).model_dump(mode="json")


def build_typed_ack(
    operation_identity: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    *,
    submission_state: Literal["ACCEPTED", "IN_PROGRESS_REPLAY", "REPLAY"],
) -> dict[str, Any]:
    """从 frozen async request 构造共用 ACK。"""

    try:
        operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    except KeyError as exc:
        raise ValueError("unsupported operation_identity") from exc
    if operation.completion_mode is not WmsCompletionMode.ASYNC_TASK:
        raise ValueError("typed ACK only accepts ASYNC_TASK operation")
    validate_json_payload(operation.request_model, payload)
    provider_digest = hashlib.sha256(f"{operation_identity}\n{idempotency_key}".encode()).hexdigest()[:16]
    ack = WmsEffectAck(
        operation_identity=operation_identity,
        idempotency_key=idempotency_key,
        provider_reference=f"mock-wms:{provider_digest}",
        submission_state=submission_state,
    )
    return ack.model_dump(mode="json")


def _not_found_snapshot() -> NorthboundStatusSnapshot:
    return NorthboundStatusSnapshot(
        state="NOT_FOUND",
        provider_reference=None,
        reason_code=None,
        updated_at=None,
        source_version=None,
        result_payload=None,
    )


__all__ = [
    "ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE",
    "MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V1",
    "MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE_V2",
    "MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1",
    "MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2",
    "NorthboundAuthError",
    "NorthboundHmacReplayGuard",
    "NorthboundOperationStore",
    "NorthboundPayloadValidationError",
    "NorthboundStatusSnapshot",
    "NorthboundSubmission",
    "build_typed_ack",
    "build_typed_result",
    "canonical_payload_bytes",
    "canonical_status_string",
    "canonical_submit_string",
    "content_sha256",
    "resolve_mock_northbound_credential",
    "validate_typed_request",
    "verify_status_hmac",
    "verify_submit_hmac",
]
