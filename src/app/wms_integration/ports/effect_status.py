"""WMS EFFECT 状态查询的冻结请求、typed 快照与 Port。"""

from __future__ import annotations

import re
from datetime import datetime  # noqa: TC003 - Pydantic 运行时需要解析该类型
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256
from src.app.wms_integration.operation_registry import (
    ASYNC_EFFECT_OPERATION_IDENTITIES,
    ASYNC_EFFECT_OPERATIONS,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    BATCH_FULFILLMENT_OPERATION_IDENTITIES,
    ChangeRackFaceRequest,
    ChangeRackFaceResult,
    RequestLoadUnitTransportRequest,
    RequestLoadUnitTransportResult,
    RequestRackSupplyRequest,
    RequestRackSupplyResult,
    RequestRackTransportRequest,
    RequestRackTransportResult,
    WmsEffectAck,
    validate_batch_terminal_result,
    validate_effect_ack,
    validate_effect_provider_reference,
    validate_fulfillment_ack,
)
from src.app.wms_integration.ports.operation_common import validate_json_payload

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StableReasonCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$"),
]
OperationIdentity = Annotated[str, StringConstraints(pattern=r"^wms\.[a-z0-9_]+\.[a-z0-9_]+@v[1-9][0-9]*$")]
WMS_EFFECT_OPERATION_IDENTITIES = ASYNC_EFFECT_OPERATION_IDENTITIES
_OPERATION_BY_IDENTITY = MappingProxyType({operation.identity: operation for operation in ASYNC_EFFECT_OPERATIONS})
_RESULT_TYPE_BY_OPERATION = MappingProxyType(
    {operation.identity: operation.result_model for operation in ASYNC_EFFECT_OPERATIONS}
)
_REJECTION_REASON_CODES_BY_OPERATION = MappingProxyType(
    {operation.identity: frozenset(operation.reject_codes) for operation in ASYNC_EFFECT_OPERATIONS}
)
_RFC3339_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00$")
BATCH_EFFECT_OPERATION_IDENTITIES = BATCH_FULFILLMENT_OPERATION_IDENTITIES


def _preserve_opaque_idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or not value or not value.strip() or "\r" in value or "\n" in value:
        raise ValueError("idempotency_key must be a non-empty single-line string")
    return value


class WmsEffectStatusRequest(BaseModel):
    """本地 typed 查询请求；原始 EFFECT payload 仅用于终态关联校验。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_identity: OperationIdentity
    idempotency_key: str = Field(min_length=1, max_length=160)
    attempt_count: int = Field(default=1, ge=1, exclude=True)
    request_payload: dict[str, Any] = Field(exclude=True)
    frozen_ack: WmsEffectAck = Field(exclude=True)

    @model_validator(mode="after")
    def validate_async_operation_request(self) -> WmsEffectStatusRequest:
        operation = _OPERATION_BY_IDENTITY.get(self.operation_identity)
        if operation is None:
            raise ValueError("operation_identity is not an authored async WMS EFFECT")
        try:
            validated = validate_json_payload(operation.request_model, self.request_payload)
        except ValidationError as exc:
            raise ValueError("request_payload violates the async operation request contract") from exc
        object.__setattr__(self, "request_payload", validated.model_dump(mode="json"))
        validate_effect_ack(
            operation_identity=self.operation_identity,
            idempotency_key=self.idempotency_key,
            ack=self.frozen_ack,
        )
        if self.operation_identity in BATCH_EFFECT_OPERATION_IDENTITIES and type(self) is WmsEffectStatusRequest:
            raise ValueError("batch status request requires frozen request and ACK context")
        return self

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def preserve_opaque_idempotency_key(cls, value: Any) -> str:
        return _preserve_opaque_idempotency_key(value)

    @property
    def query_params(self) -> dict[str, str]:
        return {
            "operation_identity": self.operation_identity,
            "idempotency_key": self.idempotency_key,
        }

    @property
    def expected_result_fields(self) -> dict[str, Any]:
        operation = _OPERATION_BY_IDENTITY[self.operation_identity]
        return {
            field_name: self.request_payload[field_name]
            for field_name in operation.result_model.model_fields
            if field_name in self.request_payload
            and not isinstance(self.request_payload[field_name], (dict, list, tuple))
        }


class WmsBatchEffectStatusRequest(WmsEffectStatusRequest):
    """E12/E13 status parser 的完整冻结上下文；ACK 不允许缺省。"""

    @model_validator(mode="after")
    def validate_frozen_batch_context(self) -> WmsBatchEffectStatusRequest:
        if self.operation_identity not in BATCH_EFFECT_OPERATION_IDENTITIES:
            raise ValueError("batch status context only supports E12/E13")
        operation = _OPERATION_BY_IDENTITY[self.operation_identity]
        batch_request = validate_json_payload(operation.request_model, self.request_payload)
        validate_fulfillment_ack(batch_request, self.frozen_ack)  # type: ignore[arg-type]
        return self


class WmsEffectStatus(str, Enum):
    """兼容字符串序列化的五态闭集。"""

    ACCEPTED = "ACCEPTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    NOT_FOUND = "NOT_FOUND"


class _WmsEffectStatusWireSnapshot(BaseModel):
    """有界 JSON 解码后的唯一 wire schema。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED", "NOT_FOUND"]
    provider_reference: StableText | None
    reason_code: StableReasonCode | None
    updated_at: datetime | None
    source_version: int | None = Field(ge=0, strict=True)
    result_payload: dict[str, Any] | None

    @field_validator("updated_at", mode="before")
    @classmethod
    def require_rfc3339_utc_timestamp(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str) or not _RFC3339_UTC_TIMESTAMP_RE.fullmatch(value):
            raise ValueError("updated_at must be an RFC 3339 UTC timestamp with +00:00 offset")
        return value


class WmsEffectStatusSnapshot(BaseModel):
    """通过 operation-specific result model 校验后的领域快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    operation_identity: OperationIdentity
    idempotency_key: str = Field(min_length=1, max_length=160)
    state: WmsEffectStatus
    provider_reference: StableText | None = None
    reason_code: StableReasonCode | None = None
    updated_at: datetime | None = None
    source_version: int | None = Field(default=None, ge=0, strict=True)
    result: SerializeAsAny[BaseModel] | None = None

    @field_validator("operation_identity")
    @classmethod
    def require_async_effect_identity(cls, value: str) -> str:
        if value not in ASYNC_EFFECT_OPERATION_IDENTITIES:
            raise ValueError("operation_identity is not an authored async WMS EFFECT")
        return value

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def preserve_opaque_idempotency_key(cls, value: Any) -> str:
        return _preserve_opaque_idempotency_key(value)

    @model_validator(mode="after")
    def validate_domain_invariants(self) -> WmsEffectStatusSnapshot:
        visible_fields = (
            self.provider_reference,
            self.reason_code,
            self.updated_at,
            self.source_version,
            self.result,
        )
        if self.state == WmsEffectStatus.NOT_FOUND:
            if any(value is not None for value in visible_fields):
                raise ValueError("NOT_FOUND must not carry visible status fields")
            return self

        if self.provider_reference is None or self.updated_at is None or self.source_version is None:
            raise ValueError("visible WMS status requires provider_reference, updated_at and source_version")
        utc_offset = self.updated_at.utcoffset()
        if utc_offset is None or utc_offset.total_seconds() != 0:
            raise ValueError("visible WMS status updated_at must be offset-aware UTC")

        if self.state == WmsEffectStatus.COMPLETED:
            if self.result is None:
                raise ValueError("COMPLETED status requires a typed result")
            expected_result_type = _RESULT_TYPE_BY_OPERATION[self.operation_identity]
            if not isinstance(self.result, expected_result_type):
                raise ValueError("COMPLETED result does not match operation_identity")
            inner_source_version = getattr(self.result, "source_version", None)
            if inner_source_version is not None and inner_source_version != str(self.source_version):
                raise ValueError("COMPLETED result source version conflicts with source_version")
        elif self.result is not None:
            raise ValueError("only COMPLETED status may carry a result")

        if self.state == WmsEffectStatus.REJECTED:
            if self.reason_code is None:
                raise ValueError("REJECTED status requires a stable reason_code")
            if self.reason_code not in _REJECTION_REASON_CODES_BY_OPERATION[self.operation_identity]:
                raise ValueError("REJECTED reason_code is not authored for the operation")
        elif self.reason_code is not None:
            raise ValueError("only REJECTED status may carry reason_code")
        return self

    @classmethod
    def not_found(cls, request: WmsEffectStatusRequest) -> WmsEffectStatusSnapshot:
        return cls(
            operation_identity=request.operation_identity,
            idempotency_key=request.idempotency_key,
            state=WmsEffectStatus.NOT_FOUND,
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json", exclude_none=False))


def _validate_result_identity(
    *,
    request: WmsEffectStatusRequest,
    result: BaseModel,
) -> None:
    expected_fields = request.expected_result_fields
    actual_fields = result.model_dump(mode="json")
    if any(actual_fields.get(field_name) != expected_value for field_name, expected_value in expected_fields.items()):
        raise ValueError("completed result identity differs from the original request")
    operation = _OPERATION_BY_IDENTITY[request.operation_identity]
    typed_request = validate_json_payload(operation.request_model, request.request_payload)
    if (
        isinstance(typed_request, RequestRackSupplyRequest)
        and isinstance(result, RequestRackSupplyResult)
        and result.task_outcome == "SUCCESS"
        and result.final_station_code != typed_request.station_code
    ):
        raise ValueError("successful rack supply final station differs from request")
    if (
        isinstance(typed_request, RequestRackTransportRequest)
        and isinstance(result, RequestRackTransportResult)
        and result.task_outcome == "SUCCESS"
        and result.final_location_code != typed_request.destination_station_code
    ):
        raise ValueError("successful rack transport final location differs from request")
    if (
        isinstance(typed_request, ChangeRackFaceRequest)
        and isinstance(result, ChangeRackFaceResult)
        and result.task_outcome == "SUCCESS"
        and (
            result.authorized_face != typed_request.requested_face or result.final_face != typed_request.requested_face
        )
    ):
        raise ValueError("successful rack face authorized/final face differs from request")
    if (
        isinstance(typed_request, RequestLoadUnitTransportRequest)
        and isinstance(result, RequestLoadUnitTransportResult)
        and result.task_outcome == "SUCCESS"
        and result.final_location_code != typed_request.destination_location_code
    ):
        raise ValueError("successful load unit final location differs from request")


def _parse_completed_result(
    *,
    request: WmsEffectStatusRequest,
    result_payload: dict[str, Any],
    source_version: int,
) -> BaseModel:
    result_model = _RESULT_TYPE_BY_OPERATION.get(request.operation_identity)
    if result_model is None:
        raise ValueError("unknown WMS EFFECT operation identity")
    try:
        result = validate_json_payload(result_model, result_payload)
    except ValidationError as exc:
        raise ValueError("completed result_payload violates the operation result contract") from exc
    inner_source_version = getattr(result, "source_version", None)
    if inner_source_version is not None and inner_source_version != str(source_version):
        raise ValueError("completed result source version conflicts with the outer source_version")
    _validate_result_identity(request=request, result=result)
    validate_effect_provider_reference(
        request.frozen_ack,
        result.provider_reference,
        evidence_kind="terminal",
    )
    if request.operation_identity in BATCH_EFFECT_OPERATION_IDENTITIES:
        if not isinstance(request, WmsBatchEffectStatusRequest):
            raise ValueError("batch terminal parsing requires frozen ACK context")
        operation = _OPERATION_BY_IDENTITY[request.operation_identity]
        batch_request = validate_json_payload(operation.request_model, request.request_payload)
        validate_batch_terminal_result(
            batch_request,  # type: ignore[arg-type]
            request.frozen_ack,
            result,  # type: ignore[arg-type]
        )
    return result  # type: ignore[return-value]


def parse_wms_effect_status_snapshot(
    *,
    request: WmsEffectStatusRequest,
    raw_response: Any,
    max_result_payload_bytes: int = 4096,
) -> WmsEffectStatusSnapshot:
    """把开放 wire JSON 收敛为只含 typed result 的不可变快照。"""

    if max_result_payload_bytes <= 0:
        raise ValueError("max_result_payload_bytes must be positive")
    wire = _WmsEffectStatusWireSnapshot.model_validate(raw_response)
    state = WmsEffectStatus(wire.state)
    if state == WmsEffectStatus.NOT_FOUND:
        if any(
            value is not None
            for value in (
                wire.provider_reference,
                wire.reason_code,
                wire.updated_at,
                wire.source_version,
                wire.result_payload,
            )
        ):
            raise ValueError("NOT_FOUND must not carry visible status fields")
        return WmsEffectStatusSnapshot.not_found(request)

    if wire.provider_reference is None or wire.updated_at is None or wire.source_version is None:
        raise ValueError("visible WMS status requires provider_reference, updated_at and source_version")
    validate_effect_provider_reference(request.frozen_ack, wire.provider_reference, evidence_kind="status")
    utc_offset = wire.updated_at.utcoffset()
    if utc_offset is None or utc_offset.total_seconds() != 0:
        raise ValueError("visible WMS status updated_at must be offset-aware UTC")

    result: BaseModel | None = None
    if state == WmsEffectStatus.COMPLETED:
        if wire.result_payload is None:
            raise ValueError("COMPLETED status requires result_payload")
        if len(canonical_json_bytes(wire.result_payload)) > max_result_payload_bytes:
            raise ValueError("COMPLETED result_payload exceeds the configured size limit")
        result = _parse_completed_result(
            request=request,
            result_payload=wire.result_payload,
            source_version=wire.source_version,
        )
    elif wire.result_payload is not None:
        raise ValueError("only COMPLETED status may carry result_payload")

    if state == WmsEffectStatus.REJECTED:
        if wire.reason_code is None:
            raise ValueError("REJECTED status requires a stable reason_code")
        if wire.reason_code not in _REJECTION_REASON_CODES_BY_OPERATION[request.operation_identity]:
            raise ValueError("REJECTED reason_code is not authored for the operation")
    elif wire.reason_code is not None:
        raise ValueError("only REJECTED status may carry reason_code")

    return WmsEffectStatusSnapshot(
        operation_identity=request.operation_identity,
        idempotency_key=request.idempotency_key,
        state=state,
        provider_reference=wire.provider_reference,
        reason_code=wire.reason_code,
        updated_at=wire.updated_at,
        source_version=wire.source_version,
        result=result,
    )


def assert_status_snapshot_progression(
    *,
    previous: WmsEffectStatusSnapshot | None,
    current: WmsEffectStatusSnapshot,
) -> WmsEffectStatusSnapshot:
    """验证同一查询键的 source version 不回退且同版本内容不可漂移。"""

    if previous is None:
        return current
    if previous.operation_identity != current.operation_identity or previous.idempotency_key != current.idempotency_key:
        raise ValueError("status snapshots do not belong to the same query key")
    if previous.source_version is not None and current.state == WmsEffectStatus.NOT_FOUND:
        raise ValueError("NOT_FOUND must not clear a previously visible source_version")
    if previous.source_version is None:
        return current
    if previous.state in {WmsEffectStatus.COMPLETED, WmsEffectStatus.REJECTED} and current.state in {
        WmsEffectStatus.ACCEPTED,
        WmsEffectStatus.PROCESSING,
    }:
        raise ValueError("terminal WMS status must not regress to a non-terminal state")
    if current.source_version is None or current.source_version < previous.source_version:
        raise ValueError("WMS status source_version must not regress")
    if current.source_version == previous.source_version and current.canonical_bytes != previous.canonical_bytes:
        raise ValueError("same source_version must keep the same canonical status snapshot")
    return current


class WmsEffectStatusTargetSnapshot(BaseModel):
    """状态查询 Intent 冻结的非秘密 HTTP target。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: StableText
    http_method: Literal["GET"] = "GET"
    timeout_seconds: float = Field(gt=0)
    max_response_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_url(self) -> WmsEffectStatusTargetSnapshot:
        parsed = urlparse(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("status target must be a non-secret HTTP(S) URL without query or fragment")
        return self


class FrozenWmsEffectStatusBinding(BaseModel):
    """Intent 持久化前生成的 typed、带 hash 状态查询 binding。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_profile_identity: StableText
    provider_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    target: WmsEffectStatusTargetSnapshot
    target_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    auth_scheme: Literal["HMAC_SHA256"]
    credential_reference: str = Field(pattern=r"^[a-z][a-z0-9+.-]*://[^@\s]+@v[1-9][0-9]*$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def snapshot(self) -> dict[str, Any]:
        return {
            "auth_scheme": self.auth_scheme,
            "binding_revision": self.binding_revision,
            "credential_reference": self.credential_reference,
            "provider_profile_hash": self.provider_profile_hash,
            "provider_profile_identity": self.provider_profile_identity,
            "target": self.target.model_dump(mode="json"),
            "target_hash": self.target_hash,
        }

    def as_persisted(self) -> dict[str, Any]:
        return {"snapshot": self.snapshot(), "snapshot_hash": self.snapshot_hash}

    @classmethod
    def from_persisted(
        cls,
        *,
        snapshot: Any,
        snapshot_hash: str,
    ) -> FrozenWmsEffectStatusBinding:
        if not isinstance(snapshot, dict):
            raise TypeError("status binding snapshot must be an object")
        if payload_sha256(canonical_json_bytes(snapshot)) != snapshot_hash:
            raise ValueError("status binding snapshot hash mismatch")
        target = WmsEffectStatusTargetSnapshot.model_validate(snapshot.get("target"))
        if payload_sha256(canonical_json_bytes(target.model_dump(mode="json"))) != snapshot.get("target_hash"):
            raise ValueError("status target snapshot hash mismatch")
        binding = cls(**snapshot, snapshot_hash=snapshot_hash)
        if binding.snapshot() != snapshot:
            raise ValueError("status binding snapshot is not in canonical typed form")
        return binding


def build_wms_effect_status_binding(*, settings_source: Any) -> FrozenWmsEffectStatusBinding:
    """从当前唯一 active WMS profile 生成新 Intent 使用的状态 binding。"""

    from src.app.runtime.system_capabilities.wms.provider_catalog import (
        WMS_EXTERNAL_HTTP_EFFECT_PROFILE,
        WMS_PROVIDER_PROFILE,
        build_active_wms_provider_profile,
    )

    configured_profile = build_active_wms_provider_profile(settings_source)
    profile = WMS_EXTERNAL_HTTP_EFFECT_PROFILE
    profile_identity = WMS_PROVIDER_PROFILE.identity.identity
    if configured_profile != WMS_PROVIDER_PROFILE:
        raise ValueError("status binding Settings must match the process active WMS provider profile")
    credential_references = frozenset(binding.credential_reference for binding in profile.bindings)
    auth_schemes = frozenset(binding.auth_scheme for binding in profile.bindings)
    if len(credential_references) != 1 or len(auth_schemes) != 1:
        raise ValueError("active WMS EFFECT profile must use one status credential revision")
    target = WmsEffectStatusTargetSnapshot(
        url=settings_source.WMS_EFFECT_STATUS_URL,
        timeout_seconds=settings_source.WMS_EFFECT_STATUS_TIMEOUT_SECONDS,
        max_response_bytes=settings_source.WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES,
    )
    target_json = target.model_dump(mode="json")
    target_hash = payload_sha256(canonical_json_bytes(target_json))
    binding_revision = payload_sha256(
        canonical_json_bytes(
            {
                "auth_scheme": next(iter(auth_schemes)),
                "credential_reference": next(iter(credential_references)),
                "provider_profile_hash": profile.profile_hash,
                "target_hash": target_hash,
            }
        )
    )
    snapshot = {
        "auth_scheme": next(iter(auth_schemes)),
        "binding_revision": binding_revision,
        "credential_reference": next(iter(credential_references)),
        "provider_profile_hash": profile.profile_hash,
        "provider_profile_identity": profile_identity,
        "target": target_json,
        "target_hash": target_hash,
    }
    return FrozenWmsEffectStatusBinding(
        **snapshot,
        snapshot_hash=payload_sha256(canonical_json_bytes(snapshot)),
    )


class WmsEffectStatusQueryPort(Protocol):
    """状态查询只接收冻结关联键并返回 typed snapshot。"""

    async def query_status(self, request: WmsEffectStatusRequest) -> WmsEffectStatusSnapshot: ...


__all__ = [
    "BATCH_EFFECT_OPERATION_IDENTITIES",
    "WMS_EFFECT_OPERATION_IDENTITIES",
    "FrozenWmsEffectStatusBinding",
    "WmsBatchEffectStatusRequest",
    "WmsEffectStatus",
    "WmsEffectStatusQueryPort",
    "WmsEffectStatusRequest",
    "WmsEffectStatusSnapshot",
    "WmsEffectStatusTargetSnapshot",
    "assert_status_snapshot_progression",
    "build_wms_effect_status_binding",
    "parse_wms_effect_status_snapshot",
]
