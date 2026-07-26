"""WMS EFFECT 状态查询的冻结请求、typed 快照与 Port。"""

from __future__ import annotations

import re
from datetime import datetime  # noqa: TC003 - Pydantic 运行时需要解析该类型
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256
from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationResult
from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationResult
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationResult

CONFIRM_INBOUND_OPERATION_IDENTITY = "wms.inventory.confirm_inbound@v1"
FULL_BOX_EXCHANGE_OPERATION_IDENTITY = "wms.fulfillment.full_box_exchange@v1"
NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY = "wms.fulfillment.notify_pkg_binding@v1"
WMS_EFFECT_OPERATION_IDENTITIES = frozenset(
    {
        CONFIRM_INBOUND_OPERATION_IDENTITY,
        FULL_BOX_EXCHANGE_OPERATION_IDENTITY,
        NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
    }
)

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StableReasonCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$"),
]
OperationIdentity = Literal[
    "wms.inventory.confirm_inbound@v1",
    "wms.fulfillment.full_box_exchange@v1",
    "wms.fulfillment.notify_pkg_binding@v1",
]


class ConfirmInboundResultIdentity(BaseModel):
    """入库确认结果中必须与原请求一致的关联字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    inbound_key: StableText = Field(max_length=120)


class FullBoxExchangeResultIdentity(BaseModel):
    """满箱交换结果中必须与原请求一致的关联字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    rack_id: StableText = Field(max_length=120)
    empty_box_id: StableText = Field(max_length=120)
    full_box_id: StableText = Field(max_length=120)


class NotifyPackageBindingResultIdentity(BaseModel):
    """料盘绑定结果中必须与原请求一致的关联字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    package_id: StableText = Field(max_length=120)
    pallet_id: StableText = Field(max_length=120)


type WmsEffectResultIdentity = (
    ConfirmInboundResultIdentity | FullBoxExchangeResultIdentity | NotifyPackageBindingResultIdentity
)
type WmsEffectOperationResult = (
    ConfirmInboundOperationResult | FullBoxExchangeOperationResult | NotifyPackageBindingOperationResult
)

_IDENTITY_TYPE_BY_OPERATION = MappingProxyType(
    {
        CONFIRM_INBOUND_OPERATION_IDENTITY: ConfirmInboundResultIdentity,
        FULL_BOX_EXCHANGE_OPERATION_IDENTITY: FullBoxExchangeResultIdentity,
        NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY: NotifyPackageBindingResultIdentity,
    }
)
_RESULT_TYPE_BY_OPERATION = MappingProxyType(
    {
        CONFIRM_INBOUND_OPERATION_IDENTITY: ConfirmInboundOperationResult,
        FULL_BOX_EXCHANGE_OPERATION_IDENTITY: FullBoxExchangeOperationResult,
        NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY: NotifyPackageBindingOperationResult,
    }
)
_REJECTION_REASON_CODES_BY_OPERATION = MappingProxyType(
    {
        CONFIRM_INBOUND_OPERATION_IDENTITY: frozenset({"MATERIAL_BLOCKED", "WMS_BUSINESS_REJECTED"}),
        FULL_BOX_EXCHANGE_OPERATION_IDENTITY: frozenset({"RACK_LOCKED", "WMS_BUSINESS_REJECTED"}),
        NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY: frozenset({"WMS_BUSINESS_REJECTED"}),
    }
)
_RFC3339_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00$")


def _preserve_opaque_idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or not value or not value.strip() or "\r" in value or "\n" in value:
        raise ValueError("idempotency_key must be a non-empty single-line string")
    return value


class WmsEffectStatusRequest(BaseModel):
    """本地 typed 查询请求；HTTP query string 只暴露两项冻结关联键。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_identity: OperationIdentity
    idempotency_key: str = Field(min_length=1, max_length=160)
    attempt_count: int = Field(default=1, ge=1, exclude=True)
    expected_result_identity: WmsEffectResultIdentity = Field(exclude=True)

    @model_validator(mode="after")
    def validate_expected_result_identity(self) -> WmsEffectStatusRequest:
        expected_type = _IDENTITY_TYPE_BY_OPERATION[self.operation_identity]
        if not isinstance(self.expected_result_identity, expected_type):
            raise TypeError("expected result identity does not match operation_identity")
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
    result: WmsEffectOperationResult | None = None

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
            if self.result.accepted is not True:
                raise ValueError("COMPLETED result requires strict accepted=true")
            inner_source_version = self.result.source_version
            if inner_source_version is not None and inner_source_version != str(self.source_version):
                raise ValueError("COMPLETED result source version conflicts with source_version")
        elif self.result is not None:
            raise ValueError("only COMPLETED status may carry a result")

        if self.state == WmsEffectStatus.REJECTED:
            if self.reason_code is None:
                raise ValueError("REJECTED status requires a stable reason_code")
            if self.reason_code not in _REJECTION_REASON_CODES_BY_OPERATION[self.operation_identity]:
                raise ValueError("REJECTED reason_code is not authored for the operation")
        elif self.state in {WmsEffectStatus.ACCEPTED, WmsEffectStatus.PROCESSING} and self.reason_code is not None:
            raise ValueError("non-terminal status must not carry reason_code")
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
    expected_fields = request.expected_result_identity.model_dump(mode="json")
    actual_fields = result.model_dump(mode="json")
    if any(actual_fields.get(field_name) != expected_value for field_name, expected_value in expected_fields.items()):
        raise ValueError("completed result identity differs from the original request")


def _parse_completed_result(
    *,
    request: WmsEffectStatusRequest,
    result_payload: dict[str, Any],
    source_version: int,
) -> WmsEffectOperationResult:
    result_model = _RESULT_TYPE_BY_OPERATION.get(request.operation_identity)
    if result_model is None:
        raise ValueError("unknown WMS EFFECT operation identity")
    try:
        if result_payload.get("accepted") is not True:
            raise ValueError("completed result_payload requires strict accepted=true")
        result = result_model.model_validate(result_payload)
    except ValidationError as exc:
        raise ValueError("completed result_payload violates the operation result contract") from exc
    if result.accepted is not True:
        raise ValueError("completed result_payload requires accepted=true")
    inner_source_version = result.source_version
    if inner_source_version is not None and inner_source_version != str(source_version):
        raise ValueError("completed result source version conflicts with the outer source_version")
    _validate_result_identity(request=request, result=result)
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
    utc_offset = wire.updated_at.utcoffset()
    if utc_offset is None or utc_offset.total_seconds() != 0:
        raise ValueError("visible WMS status updated_at must be offset-aware UTC")

    result: WmsEffectOperationResult | None = None
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
    elif state in {WmsEffectStatus.ACCEPTED, WmsEffectStatus.PROCESSING} and wire.reason_code is not None:
        raise ValueError("non-terminal status must not carry reason_code")

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
    "CONFIRM_INBOUND_OPERATION_IDENTITY",
    "FULL_BOX_EXCHANGE_OPERATION_IDENTITY",
    "NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY",
    "WMS_EFFECT_OPERATION_IDENTITIES",
    "ConfirmInboundResultIdentity",
    "FrozenWmsEffectStatusBinding",
    "FullBoxExchangeResultIdentity",
    "NotifyPackageBindingResultIdentity",
    "WmsEffectStatus",
    "WmsEffectStatusQueryPort",
    "WmsEffectStatusRequest",
    "WmsEffectStatusSnapshot",
    "WmsEffectStatusTargetSnapshot",
    "assert_status_snapshot_progression",
    "build_wms_effect_status_binding",
    "parse_wms_effect_status_snapshot",
]
