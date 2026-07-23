"""EXTERNAL_HTTP 版本化 secret provider 边界。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


_REFERENCE_ENV_NAMES = {
    "secret://wms/material-flow-sandbox-hmac@v1": "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1",
    "secret://wms/material-flow-staging-hmac@v1": "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V1",
    "secret://wms/material-flow-production-hmac@v1": "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1",
    "secret://wms/legacy-transport-production-hmac@v1": "WMS_LEGACY_TRANSPORT_PRODUCTION_HMAC_SECRET_V1",
    "secret://workline/plugin-runtime-hmac@v1": "WORKLINE_PLUGIN_RUNTIME_HMAC_SECRET_V1",
}
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
    """由 allowlist 将版本化 reference 精确映射到单个环境变量。"""

    reference_env_names: Mapping[str, str]
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
        secret = os.getenv(env_name)
        if secret is None or not secret:
            raise CredentialResolutionError("CREDENTIAL_UNAVAILABLE", "credential material is unavailable")
        return secret.encode("utf-8")


def _emit_credential_resolution_audit(event: CredentialResolutionAudit) -> None:
    from src.app.runtime.orchestration.operation_observability import emit_credential_resolution_observation

    _ = emit_credential_resolution_observation(
        provider_kind=event.provider_kind,
        outcome=event.status,
    )


def build_environment_external_http_credential_provider() -> AuditedVersionedCredentialProvider:
    """从显式 allowlist 与紧急撤销清单构建 effect secret provider。"""

    revoked_references = frozenset(
        reference.strip() for reference in os.getenv(_REVOKED_REFERENCES_ENV, "").split(",") if reference.strip()
    )
    return AuditedVersionedCredentialProvider(
        EnvironmentVersionedCredentialProvider(
            reference_env_names=_REFERENCE_ENV_NAMES,
            revoked_references=revoked_references,
        ),
        provider_kind="environment",
    )


external_http_credential_provider = build_environment_external_http_credential_provider()


__all__ = [
    "AuditedVersionedCredentialProvider",
    "CredentialResolutionAudit",
    "CredentialResolutionError",
    "CredentialRevokedError",
    "EnvironmentVersionedCredentialProvider",
    "VersionedCredentialProvider",
    "build_environment_external_http_credential_provider",
    "external_http_credential_provider",
]
