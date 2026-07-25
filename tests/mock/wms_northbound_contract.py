"""WMS 北向 Mock 的认证、幂等与状态机核心。

本模块只依赖 Python 标准库，Docker Mock 镜像可独立加载，不能导入 WES 运行时。
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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
    {
        "wms.inventory.confirm_inbound@v1",
        "wms.fulfillment.full_box_exchange@v1",
        "wms.fulfillment.notify_pkg_binding@v1",
    }
)
_ALLOWED_REJECTION_REASON_CODES_BY_OPERATION = {
    "wms.inventory.confirm_inbound@v1": frozenset({"MATERIAL_BLOCKED", "WMS_BUSINESS_REJECTED"}),
    "wms.fulfillment.full_box_exchange@v1": frozenset({"RACK_LOCKED", "WMS_BUSINESS_REJECTED"}),
    "wms.fulfillment.notify_pkg_binding@v1": frozenset({"WMS_BUSINESS_REJECTED"}),
}
_REQUEST_FIELDS_BY_OPERATION = {
    "wms.inventory.confirm_inbound@v1": {
        "required": frozenset({"dispatch_key", "inbound_key", "material_code", "quantity"}),
        "optional": frozenset({"warehouse_code", "owner_code", "lot_no"}),
        "max_lengths": {
            "dispatch_key": 240,
            "inbound_key": 120,
            "material_code": 120,
            "quantity": 120,
            "warehouse_code": 120,
            "owner_code": 120,
            "lot_no": 120,
        },
    },
    "wms.fulfillment.full_box_exchange@v1": {
        "required": frozenset({"dispatch_key", "rack_id", "empty_box_id", "full_box_id"}),
        "optional": frozenset(),
        "max_lengths": {
            "dispatch_key": 240,
            "rack_id": 120,
            "empty_box_id": 120,
            "full_box_id": 120,
        },
    },
    "wms.fulfillment.notify_pkg_binding@v1": {
        "required": frozenset({"dispatch_key", "package_id", "pallet_id", "station_code"}),
        "optional": frozenset(),
        "max_lengths": {
            "dispatch_key": 240,
            "package_id": 120,
            "pallet_id": 120,
            "station_code": 120,
        },
    },
}


class NorthboundAuthError(ValueError):
    """北向 Mock 认证失败，``code`` 可直接映射为稳定 HTTP 错误码。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NorthboundPayloadValidationError(ValueError):
    """typed wire body 不满足冻结 required/allowed/type 合同。"""


def content_sha256(body: bytes) -> str:
    """返回实际 wire bytes 的小写 SHA-256 指纹。"""

    return hashlib.sha256(body).hexdigest()


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
    """在幂等写入前校验 Mock 自包含的 operation-specific wire schema。"""

    try:
        spec = _REQUEST_FIELDS_BY_OPERATION[operation_identity]
    except KeyError as exc:
        raise NorthboundPayloadValidationError("unsupported operation_identity") from exc
    fields = frozenset(payload)
    required = spec["required"]
    allowed = required | spec["optional"]
    if not required <= fields or not fields <= allowed:
        raise NorthboundPayloadValidationError("typed request fields are invalid")
    for field_name, value in payload.items():
        if not isinstance(value, str) or value != value.strip() or not value:
            raise NorthboundPayloadValidationError("typed request string is invalid")
        if len(value) > spec["max_lengths"][field_name]:
            raise NorthboundPayloadValidationError("typed request string is too long")
    if operation_identity == "wms.inventory.confirm_inbound@v1":
        try:
            quantity = Decimal(payload["quantity"])
        except (InvalidOperation, ValueError) as exc:
            raise NorthboundPayloadValidationError("quantity is not a decimal string") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise NorthboundPayloadValidationError("quantity must be a positive finite decimal")
    return dict(payload)


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in headers.items()}


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name, "").strip()
    if not value or "\r" in value or "\n" in value:
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
        visibility_sla_seconds: int = 2,
    ) -> None:
        if retention_seconds <= 0 or visibility_sla_seconds <= 0:
            raise ValueError("northbound retention and visibility SLA must be positive")
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retention_seconds = retention_seconds
        self._visibility_sla_seconds = visibility_sla_seconds
        self._records: dict[tuple[str, str], _OperationRecord] = {}
        self._pending_visibility_delays: dict[tuple[str, str], int] = {}
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
        delay_seconds: int,
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
) -> dict[str, Any]:
    """为三个冻结 operation 构造与原请求关联的 completed result。"""

    builders = {
        "wms.inventory.confirm_inbound@v1": build_confirm_inbound_result,
        "wms.fulfillment.full_box_exchange@v1": build_full_box_exchange_result,
        "wms.fulfillment.notify_pkg_binding@v1": build_package_binding_result,
    }
    try:
        builder = builders[operation_identity]
    except KeyError as exc:
        raise ValueError("unsupported operation_identity") from exc
    return builder(payload, source_version=source_version, completed_at=completed_at)


def _result_base(payload: Mapping[str, Any], *, source_version: int) -> dict[str, Any]:
    return {
        "accepted": True,
        "dispatch_key": str(payload.get("dispatch_key") or ""),
        "reason_code": None,
        "source_version": str(source_version),
    }


def build_confirm_inbound_result(
    payload: Mapping[str, Any], *, source_version: int, completed_at: str
) -> dict[str, Any]:
    # Confirm inbound 的 frozen replay schema 不携带完成时间或原始物料明细。
    del completed_at
    return {
        **_result_base(payload, source_version=source_version),
        "inbound_key": str(payload.get("inbound_key") or ""),
        "document_no": str(payload.get("document_no") or payload.get("inbound_key") or ""),
    }


def build_full_box_exchange_result(
    payload: Mapping[str, Any], *, source_version: int, completed_at: str
) -> dict[str, Any]:
    # Full-box exchange 的 frozen replay schema 不携带完成时间。
    del completed_at
    return {
        **_result_base(payload, source_version=source_version),
        "rack_id": str(payload.get("rack_id") or ""),
        "empty_box_id": str(payload.get("empty_box_id") or ""),
        "full_box_id": str(payload.get("full_box_id") or ""),
        "exchange_request_code": str(payload.get("exchange_request_code") or payload.get("dispatch_key") or ""),
    }


def build_package_binding_result(
    payload: Mapping[str, Any], *, source_version: int, completed_at: str
) -> dict[str, Any]:
    return {
        **_result_base(payload, source_version=source_version),
        "bound_at": completed_at,
        "package_id": str(payload.get("package_id") or ""),
        "pallet_id": str(payload.get("pallet_id") or ""),
    }


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
    "NorthboundOperationStore",
    "NorthboundPayloadValidationError",
    "NorthboundStatusSnapshot",
    "NorthboundSubmission",
    "build_confirm_inbound_result",
    "build_full_box_exchange_result",
    "build_package_binding_result",
    "build_typed_result",
    "canonical_status_string",
    "canonical_submit_string",
    "content_sha256",
    "resolve_mock_northbound_credential",
    "validate_typed_request",
    "verify_status_hmac",
    "verify_submit_hmac",
]
