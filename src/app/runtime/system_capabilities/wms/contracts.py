"""WMS operation catalog 的组合合同。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from src.app.wms_integration.operation_contract import WmsOperationDefinition  # noqa: TC001

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OperationIdentity = Annotated[str, StringConstraints(pattern=r"^wms\.[a-z0-9_]+\.[a-z0-9_]+@v[1-9][0-9]*$")]
_CREDENTIAL_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^@\s]+@v[1-9][0-9]*$")


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
    operation: WmsOperationDefinition
    outbound_auth: OutboundAuthProfile

    @model_validator(mode="after")
    def enforce_outbound_auth(self) -> WmsProviderOperationBinding:
        if self.profile.environment == "production" and self.outbound_auth.scheme is OutboundAuthScheme.NONE:
            raise ValueError("production operation forbids outbound auth NONE")
        return self


class InboundCallbackContract(BaseModel):
    """Provider 可选的通用 EFFECT 状态查询提示合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    callback_type: Literal["WMS_EFFECT_STATUS_HINT"]
    payload_model: type[BaseModel]

    @model_validator(mode="after")
    def enforce_effect_callback(self) -> InboundCallbackContract:
        if self.payload_model is not WmsEffectStatusHint:
            raise ValueError("WMS callback must use the generic EFFECT status hint payload")
        return self


class WmsEffectStatusHint(BaseModel):
    """不携带终态结果、只定位既有 EFFECT 的 callback hint。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_identity: OperationIdentity
    idempotency_key: StableText = Field(max_length=160)
    dispatch_key: StableText = Field(max_length=240)


class WmsProviderProfile(BaseModel):
    """External identity 与 typed operation/callback binding 的组合。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ExternalContractProfile
    bindings: tuple[WmsProviderOperationBinding, ...]
    callbacks: tuple[InboundCallbackContract, ...] = ()

    @model_validator(mode="after")
    def validate_composition(self) -> WmsProviderProfile:
        if len(self.bindings) < 2:
            raise ValueError("active provider profile must contain multiple typed operation bindings")
        if any(binding.profile != self.identity for binding in self.bindings):
            raise ValueError("all operation bindings must use provider profile identity")
        operation_identities = tuple(binding.operation.identity for binding in self.bindings)
        if len(operation_identities) != len(set(operation_identities)):
            raise ValueError("provider profile contains duplicate operation identity")
        callback_types = tuple(callback.callback_type for callback in self.callbacks)
        if len(callback_types) != len(set(callback_types)):
            raise ValueError("provider profile contains duplicate callback type")
        if len(self.callbacks) > 1:
            raise ValueError("provider profile may declare at most one generic EFFECT status hint callback")
        return self


__all__ = [
    "ExternalContractProfile",
    "InboundCallbackContract",
    "OutboundAuthProfile",
    "OutboundAuthScheme",
    "WmsEffectStatusHint",
    "WmsProviderOperationBinding",
    "WmsProviderProfile",
]
