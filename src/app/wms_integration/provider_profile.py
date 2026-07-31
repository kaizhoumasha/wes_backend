"""部署拥有、启动时只读的 WMS Provider profile 合同。"""

from __future__ import annotations

import re
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, field_serializer, model_validator

from src.app.wms_integration.operation_contract import WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS

if TYPE_CHECKING:
    from pathlib import Path

WMS_PROVIDER_CONTRACT_VERSION = "2026-07-28.full-factory"

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_CREDENTIAL_REFERENCE_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://[^@\s]+@v[1-9][0-9]*$")


def build_wms_provider_identity(provider_code: str, contract_version: str) -> str:
    """构造不含应用环境维度的 WMS 部署身份。"""

    return f"{provider_code.strip().lower()}.{contract_version}"


class WmsProviderAuthScheme(str, Enum):
    """Provider profile 支持的封闭认证方案。"""

    NONE = "NONE"
    HMAC_SHA256 = "HMAC_SHA256"


class WmsProviderIdentitySettings(BaseModel):
    """当前部署唯一 Provider 的身份与合同版本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_code: Literal["WMS"]
    contract_version: Literal[WMS_PROVIDER_CONTRACT_VERSION]

    @property
    def identity(self) -> str:
        return build_wms_provider_identity(self.provider_code, self.contract_version)


class WmsProviderAuthSettings(BaseModel):
    """不含秘密值的认证声明。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: WmsProviderAuthScheme
    credential_reference: str | None = None

    @model_validator(mode="after")
    def validate_credential_reference(self) -> WmsProviderAuthSettings:
        if self.scheme is WmsProviderAuthScheme.NONE:
            if self.credential_reference is not None:
                raise ValueError("NONE auth must not carry credential_reference")
            return self
        if self.credential_reference is None or not _CREDENTIAL_REFERENCE_PATTERN.fullmatch(self.credential_reference):
            raise ValueError("HMAC_SHA256 auth requires a versioned credential_reference")
        return self


class WmsProviderOperationPathSettings(BaseModel):
    """单项 operation 的部署相对 path；静态语义仍由 registry 拥有。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: NonEmptyText | None = None
    submit_path: NonEmptyText | None = None


class WmsProviderProfileSettings(BaseModel):
    """35 项 operation 的严格、不可变部署 profile。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: WmsProviderIdentitySettings
    server_url: NonEmptyText
    effect_status_path: NonEmptyText
    network_trust_mode: NonEmptyText
    outbound_auth: WmsProviderAuthSettings
    inbound_auth: WmsProviderAuthSettings
    operations: dict[str, WmsProviderOperationPathSettings]

    @model_validator(mode="after")
    def validate_registry_and_security(self) -> WmsProviderProfileSettings:
        expected = frozenset(operation.identity for operation in WMS_OPERATIONS)
        if frozenset(self.operations) != expected:
            raise ValueError("operations must exactly cover the static WMS operation registry")
        for identity, configured_path in self.operations.items():
            operation = WMS_OPERATION_BY_IDENTITY[identity]
            if operation.mode is WmsOperationMode.QUERY:
                if configured_path.path is None or configured_path.submit_path is not None:
                    raise ValueError(f"QUERY operation requires path only: {identity}")
            elif configured_path.submit_path is None or configured_path.path is not None:
                raise ValueError(f"EFFECT operation requires submit_path only: {identity}")
        if self.network_trust_mode != "isolated_lan":
            none_auth = (
                self.outbound_auth.scheme is WmsProviderAuthScheme.NONE
                or self.inbound_auth.scheme is WmsProviderAuthScheme.NONE
            )
            if none_auth:
                raise ValueError("NONE auth is only valid with network_trust_mode=isolated_lan")
        object.__setattr__(self, "operations", MappingProxyType(dict(self.operations)))
        return self

    @field_serializer("operations")
    def serialize_operations(
        self,
        operations: dict[str, WmsProviderOperationPathSettings],
    ) -> dict[str, WmsProviderOperationPathSettings]:
        return dict(operations)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """拒绝 YAML 重复键，避免 operation identity 被静默覆盖。"""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate key in WMS Provider profile: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_wms_provider_profile(profile_file: Path) -> WmsProviderProfileSettings:
    """从绝对文件路径读取一次 profile，不执行环境变量或模板递归替换。"""

    if not profile_file.is_absolute():
        raise ValueError("WMS_PROVIDER_PROFILE_FILE must be an absolute path")
    try:
        profile_text = profile_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("WMS Provider profile file is not readable") from exc

    # `_UniqueKeySafeLoader` 继承 SafeLoader，仅增加重复键拒绝，不允许任意对象构造。
    loader = _UniqueKeySafeLoader(profile_text)
    try:
        payload = loader.get_single_data()
    finally:
        loader.dispose()
    if not isinstance(payload, dict):
        raise TypeError("WMS Provider profile root must be a mapping")
    return WmsProviderProfileSettings.model_validate(payload)


__all__ = [
    "WMS_PROVIDER_CONTRACT_VERSION",
    "WmsProviderAuthScheme",
    "WmsProviderAuthSettings",
    "WmsProviderIdentitySettings",
    "WmsProviderOperationPathSettings",
    "WmsProviderProfileSettings",
    "build_wms_provider_identity",
    "load_wms_provider_profile",
]
