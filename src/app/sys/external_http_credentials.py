"""EXTERNAL_HTTP 版本化 secret provider 边界。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

from src.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE = MappingProxyType(
    {
        "secret://wms/material-flow-sandbox-hmac@v1": "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1",
        "secret://wms/material-flow-staging-hmac@v1": "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V1",
        "secret://wms/material-flow-production-hmac@v1": "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1",
        "secret://wms/material-flow-sandbox-hmac@v2": "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2",
        "secret://wms/material-flow-staging-hmac@v2": "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V2",
        "secret://wms/material-flow-production-hmac@v2": "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V2",
        "secret://wms/legacy-transport-sandbox-hmac@v1": "WMS_LEGACY_TRANSPORT_SANDBOX_HMAC_SECRET_V1",
        "secret://wms/legacy-transport-staging-hmac@v1": "WMS_LEGACY_TRANSPORT_STAGING_HMAC_SECRET_V1",
        "secret://wms/legacy-transport-production-hmac@v1": "WMS_LEGACY_TRANSPORT_PRODUCTION_HMAC_SECRET_V1",
        "secret://workline/plugin-runtime-sandbox-hmac@v1": "WORKLINE_PLUGIN_RUNTIME_SANDBOX_HMAC_SECRET_V1",
        "secret://workline/plugin-runtime-staging-hmac@v1": "WORKLINE_PLUGIN_RUNTIME_STAGING_HMAC_SECRET_V1",
        "secret://workline/plugin-runtime-production-hmac@v1": "WORKLINE_PLUGIN_RUNTIME_PRODUCTION_HMAC_SECRET_V1",
    }
)
_REVOKED_REFERENCES_ENV = "WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES"


class VersionedCredentialProvider(Protocol):
    """只按精确版本 reference 解析密钥材料。"""

    def resolve(self, credential_reference: str) -> bytes: ...


class CredentialResolutionError(LookupError):
    """密钥材料在外部 I/O 前无法安全解析。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CredentialRevokedError(CredentialResolutionError):
    """冻结 reference 已被紧急撤销。"""

    def __init__(self) -> None:
        super().__init__("CREDENTIAL_REVOKED", "frozen credential reference is revoked")


@dataclass(frozen=True, slots=True)
class CredentialResolutionAudit:
    """不含 reference、secret 或 header 的凭据解析审计事件。"""

    provider_kind: Literal["environment", "custom"]
    status: Literal["RESOLVED", "REVOKED", "RESOLUTION_FAILED", "PROVIDER_ERROR"]


@dataclass(frozen=True, slots=True)
class AuditedVersionedCredentialProvider:
    """以脱敏闭集事件包装任意版本化 credential provider。"""

    provider: VersionedCredentialProvider
    observer: Callable[[CredentialResolutionAudit], None] | None = None
    provider_kind: Literal["environment", "custom"] = "custom"

    def resolve(self, credential_reference: str) -> bytes:
        try:
            secret = self.provider.resolve(credential_reference)
        except CredentialRevokedError:
            self._notify("REVOKED")
            raise
        except (CredentialResolutionError, LookupError):
            self._notify("RESOLUTION_FAILED")
            raise
        except Exception:
            self._notify("PROVIDER_ERROR")
            raise
        self._notify("RESOLVED")
        return secret

    def _notify(
        self,
        status: Literal["RESOLVED", "REVOKED", "RESOLUTION_FAILED", "PROVIDER_ERROR"],
    ) -> None:
        event = CredentialResolutionAudit(provider_kind=self.provider_kind, status=status)
        try:
            if self.observer is None:
                _emit_credential_resolution_audit(event)
            else:
                self.observer(event)
        except Exception:
            # 观测后端不可用不得改变 secret provider 的解析结果或错误语义。
            return


@dataclass(frozen=True, slots=True)
class EnvironmentVersionedCredentialProvider:
    """由 allowlist 将版本化 reference 精确映射到 Settings 字段。"""

    reference_env_names: Mapping[str, str]
    settings_source: Any = field(repr=False)
    revoked_references: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_env_names", MappingProxyType(dict(self.reference_env_names)))
        object.__setattr__(self, "revoked_references", frozenset(self.revoked_references))

    def resolve(self, credential_reference: str) -> bytes:
        if credential_reference in self.revoked_references:
            raise CredentialRevokedError
        env_name = self.reference_env_names.get(credential_reference)
        if env_name is None:
            raise CredentialResolutionError("CREDENTIAL_NOT_CONFIGURED", "credential reference is not configured")
        secret = getattr(self.settings_source, env_name, "")
        if secret is None or not secret:
            raise CredentialResolutionError("CREDENTIAL_UNAVAILABLE", "credential material is unavailable")
        return secret.encode("utf-8")


def _emit_credential_resolution_audit(event: CredentialResolutionAudit) -> None:
    from src.app.runtime.orchestration.operation_observability import emit_credential_resolution_observation

    _ = emit_credential_resolution_observation(
        provider_kind=event.provider_kind,
        outcome=event.status,
    )


def build_environment_external_http_credential_provider(
    *, settings_source: Any | None = None
) -> AuditedVersionedCredentialProvider:
    """从显式 allowlist 与紧急撤销清单构建 effect secret provider。"""

    active_settings = settings if settings_source is None else settings_source
    revoked_references = frozenset(
        reference.strip()
        for reference in getattr(active_settings, _REVOKED_REFERENCES_ENV, "").split(",")
        if reference.strip()
    )
    return AuditedVersionedCredentialProvider(
        EnvironmentVersionedCredentialProvider(
            reference_env_names=EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE,
            settings_source=active_settings,
            revoked_references=revoked_references,
        ),
        provider_kind="environment",
    )


external_http_credential_provider = build_environment_external_http_credential_provider()


__all__ = [
    "EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE",
    "AuditedVersionedCredentialProvider",
    "CredentialResolutionAudit",
    "CredentialResolutionError",
    "CredentialRevokedError",
    "EnvironmentVersionedCredentialProvider",
    "VersionedCredentialProvider",
    "build_environment_external_http_credential_provider",
    "external_http_credential_provider",
]
