"""WMS operation catalog 的组合合同。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OperationIdentity = Annotated[str, StringConstraints(pattern=r"^wms\.[a-z0-9_]+\.[a-z0-9_]+@v[1-9][0-9]*$")]
FinitePositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_CREDENTIAL_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^@\s]+@v[1-9][0-9]*$")


class WmsOperationMode(str, Enum):
    """WMS operation 执行模式。"""

    QUERY = "QUERY"
    EFFECT = "EFFECT"


class WmsHttpMethod(str, Enum):
    """当前真实 WMS operation 使用的 HTTP 方法。"""

    GET = "GET"
    POST = "POST"


class OutboundAuthScheme(str, Enum):
    """封闭的 WMS 出站认证 scheme。"""

    NONE = "NONE"
    HMAC_SHA256 = "HMAC_SHA256"


class ExternalContractProfile(BaseModel):
    """只表达 provider/version/environment 的运行时 identity。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_code: StableText = Field(max_length=80)
    contract_version: StableText = Field(max_length=120)
    environment: Literal["sandbox", "staging", "production"]

    @field_validator("provider_code")
    @classmethod
    def canonicalize_provider_code(cls, value: str) -> str:
        return value.lower()

    @property
    def identity(self) -> str:
        return f"{self.provider_code}.{self.contract_version}.{self.environment}"


class WmsTransportBudget(BaseModel):
    """单 operation 的传输和解析预算。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_wire_bytes: int = Field(gt=0)
    max_decoded_bytes: int = Field(gt=0)
    max_rows: int | None = Field(default=None, gt=0)
    max_chunk_bytes: int = Field(default=262_144, gt=0)
    max_compression_ratio: float = Field(default=20.0, gt=1, allow_inf_nan=False)
    allowed_content_encodings: tuple[Literal["identity", "gzip", "deflate"], ...] = (
        "identity",
        "gzip",
        "deflate",
    )
    max_json_depth: int = Field(default=12, ge=1, le=64)
    max_field_length: int = Field(default=16_384, ge=1)

    @model_validator(mode="after")
    def decoded_budget_covers_wire_budget(self) -> WmsTransportBudget:
        if self.max_decoded_bytes < self.max_wire_bytes:
            raise ValueError("max_decoded_bytes must be greater than or equal to max_wire_bytes")
        if self.max_chunk_bytes > self.max_wire_bytes:
            raise ValueError("max_chunk_bytes must be less than or equal to max_wire_bytes")
        if not self.allowed_content_encodings:
            raise ValueError("allowed_content_encodings must not be empty")
        return self


class WmsPaginationContract(BaseModel):
    """Provider cursor pagination 的通用字段合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_cursor_field: StableText = Field(max_length=80)
    response_cursor_field: StableText = Field(max_length=80)
    response_items_field: StableText = Field(max_length=80)
    max_pages: int = Field(ge=1, le=10_000)


class WmsRetryPolicy(BaseModel):
    """单 operation 的有界重试策略。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(ge=1, le=10)
    backoff_seconds: tuple[FinitePositiveSeconds, ...] = ()

    @model_validator(mode="after")
    def backoff_matches_attempt_budget(self) -> WmsRetryPolicy:
        if len(self.backoff_seconds) != self.max_attempts - 1:
            raise ValueError("backoff_seconds count must equal max_attempts - 1")
        if any(value <= 0 for value in self.backoff_seconds):
            raise ValueError("backoff_seconds must contain positive values")
        return self


class WmsOperationContract(BaseModel):
    """单个 typed operation 的 transport 与领域模型绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    identity: OperationIdentity
    mode: WmsOperationMode
    request_model: type[BaseModel]
    result_model: type[BaseModel]
    endpoint_path: StableText = Field(pattern=r"^/")
    target_code: StableText = Field(max_length=120)
    http_method: WmsHttpMethod
    budget: WmsTransportBudget
    retry_policy: WmsRetryPolicy
    outbound_auth_scheme: OutboundAuthScheme
    pagination: WmsPaginationContract | None = None

    @model_validator(mode="after")
    def enforce_contract_ownership(self) -> WmsOperationContract:
        for model in (self.request_model, self.result_model):
            if not model.__module__.startswith("src.app.wms_integration.ports."):
                raise ValueError("operation request/result model must be owned by wms_integration.ports")
        if self.mode is WmsOperationMode.QUERY and self.budget.max_rows is None:
            raise ValueError("QUERY operation requires max_rows budget")
        if self.mode is WmsOperationMode.QUERY and self.pagination is None:
            raise ValueError("QUERY operation requires pagination contract")
        if self.mode is WmsOperationMode.EFFECT and self.budget.max_rows is not None:
            raise ValueError("EFFECT operation must not declare query row budget")
        if self.mode is WmsOperationMode.EFFECT and self.pagination is not None:
            raise ValueError("EFFECT operation must not declare pagination contract")
        if self.outbound_auth_scheme is OutboundAuthScheme.NONE:
            raise ValueError("production-capable operation contract cannot require outbound auth NONE")
        return self


class OutboundAuthProfile(BaseModel):
    """Provider binding 选择的版本化出站 credential reference。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: OutboundAuthScheme
    credential_reference: str | None = None

    @model_validator(mode="after")
    def validate_credential_reference(self) -> OutboundAuthProfile:
        if self.scheme is OutboundAuthScheme.NONE:
            if self.credential_reference is not None:
                raise ValueError("NONE auth must not carry credential_reference")
            return self
        if self.credential_reference is None or not _CREDENTIAL_REFERENCE_RE.fullmatch(self.credential_reference):
            raise ValueError("authenticated profile requires a versioned credential_reference")
        return self


class WmsProviderOperationBinding(BaseModel):
    """Provider identity、operation 与 credential reference 的不可变组合。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: ExternalContractProfile
    operation: WmsOperationContract
    outbound_auth: OutboundAuthProfile

    @model_validator(mode="after")
    def enforce_outbound_auth(self) -> WmsProviderOperationBinding:
        if self.profile.environment == "production" and self.outbound_auth.scheme is OutboundAuthScheme.NONE:
            raise ValueError("production operation forbids outbound auth NONE")
        if self.outbound_auth.scheme is not self.operation.outbound_auth_scheme:
            raise ValueError("binding auth scheme must match operation contract")
        return self


class InboundCallbackContract(BaseModel):
    """单个 EFFECT 的独立入站 callback 合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    operation: WmsOperationContract
    callback_type: StableText = Field(max_length=120)
    payload_model: type[BaseModel]

    @model_validator(mode="after")
    def enforce_effect_callback(self) -> InboundCallbackContract:
        if self.operation.mode is not WmsOperationMode.EFFECT:
            raise ValueError("only EFFECT operation may declare callback contract")
        if self.payload_model is not self.operation.result_model:
            raise ValueError("callback payload_model must reuse operation result_model")
        return self


class WmsProviderProfile(BaseModel):
    """External identity 与 typed operation/callback binding 的组合。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ExternalContractProfile
    bindings: tuple[WmsProviderOperationBinding, ...]
    callbacks: tuple[InboundCallbackContract, ...]

    @model_validator(mode="after")
    def validate_composition(self) -> WmsProviderProfile:
        if any(binding.profile != self.identity for binding in self.bindings):
            raise ValueError("all operation bindings must use provider profile identity")
        operation_identities = tuple(binding.operation.identity for binding in self.bindings)
        if len(operation_identities) != len(set(operation_identities)):
            raise ValueError("provider profile contains duplicate operation identity")
        bound_effects = {
            binding.operation.identity: binding.operation
            for binding in self.bindings
            if binding.operation.mode is WmsOperationMode.EFFECT
        }
        callback_identities = tuple(callback.operation.identity for callback in self.callbacks)
        if len(callback_identities) != len(set(callback_identities)) or set(callback_identities) != set(bound_effects):
            raise ValueError("every bound EFFECT must declare exactly one callback contract")
        if any(
            callback.operation is not bound_effects[callback.operation.identity]
            and callback.operation != bound_effects[callback.operation.identity]
            for callback in self.callbacks
        ):
            raise ValueError("callback must reference the bound EFFECT operation contract")
        return self


__all__ = [
    "ExternalContractProfile",
    "InboundCallbackContract",
    "OutboundAuthProfile",
    "OutboundAuthScheme",
    "WmsHttpMethod",
    "WmsOperationContract",
    "WmsOperationMode",
    "WmsPaginationContract",
    "WmsProviderOperationBinding",
    "WmsProviderProfile",
    "WmsRetryPolicy",
    "WmsTransportBudget",
]
