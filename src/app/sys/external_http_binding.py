"""EXTERNAL_HTTP Provider binding 与非秘密 target 冻结合同。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256
from src.utils.value_normalization import require_string

if TYPE_CHECKING:
    from src.app.sys.services.endpoint_registry import EndpointRegistry

_CREDENTIAL_REFERENCE_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^@\s]+@v[1-9][0-9]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_AUTH_SCHEMES = frozenset({"HMAC_SHA256"})
_TARGET_SNAPSHOT_FIELDS = frozenset({"code", "url", "http_method", "timeout_seconds"})


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _persisted_auth_scheme(value: object) -> Literal["HMAC_SHA256"]:
    """恢复受支持的认证方案，同时保留闭集类型。"""

    if value != "HMAC_SHA256":
        raise ValueError("unsupported frozen outbound auth scheme")
    return "HMAC_SHA256"


def _sha256_json(value: Any) -> str:
    return payload_sha256(canonical_json_bytes(value))


@dataclass(frozen=True, slots=True)
class ExternalHttpBindingDefinition:
    """Provider profile 内单个 operation 的 author-time binding。"""

    operation_identity: str
    allowed_target_codes: tuple[str, ...]
    http_method: Literal["POST"]
    timeout_seconds: float
    auth_scheme: Literal["HMAC_SHA256"]
    credential_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_identity", _required_text(self.operation_identity, "operation_identity"))
        target_codes = tuple(_required_text(code, "allowed target code") for code in self.allowed_target_codes)
        if not target_codes or len(target_codes) != len(set(target_codes)):
            raise ValueError("binding requires unique authored target codes")
        object.__setattr__(self, "allowed_target_codes", target_codes)
        if self.http_method != "POST":
            raise ValueError("EXTERNAL_HTTP effect binding only supports POST")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("binding timeout_seconds must be positive")
        if self.auth_scheme not in _SUPPORTED_AUTH_SCHEMES:
            raise ValueError("unsupported outbound auth scheme")
        if not _CREDENTIAL_REFERENCE_RE.fullmatch(self.credential_reference):
            raise ValueError("binding requires a versioned credential reference")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "allowed_target_codes": self.allowed_target_codes,
            "auth_scheme": self.auth_scheme,
            "credential_reference": self.credential_reference,
            "http_method": self.http_method,
            "operation_identity": self.operation_identity,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ExternalHttpProviderProfileDefinition:
    """仅含非秘密声明的 EXTERNAL_HTTP Provider profile。"""

    identity: str
    environment: Literal["sandbox", "staging", "production"]
    bindings: tuple[ExternalHttpBindingDefinition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _required_text(self.identity, "provider profile identity"))
        if self.environment not in {"sandbox", "staging", "production"}:
            raise ValueError("provider profile environment is invalid")
        identities = tuple(binding.operation_identity for binding in self.bindings)
        if not identities or len(identities) != len(set(identities)):
            raise ValueError("provider profile requires unique operation bindings")

    @property
    def profile_hash(self) -> str:
        return _sha256_json(
            {
                "bindings": tuple(binding.canonical_payload() for binding in self.bindings),
                "environment": self.environment,
                "identity": self.identity,
            }
        )

    def resolve_binding(self, operation_identity: str) -> ExternalHttpBindingDefinition:
        matches = tuple(binding for binding in self.bindings if binding.operation_identity == operation_identity)
        if len(matches) != 1:
            raise ValueError("operation binding must resolve exactly once from authored profile")
        return matches[0]


@dataclass(frozen=True, slots=True)
class ExternalHttpTargetSnapshot:
    """发送与重试唯一允许读取的非秘密 endpoint 快照。"""

    code: str
    url: str
    http_method: Literal["POST"]
    timeout_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required_text(self.code, "target snapshot code"))
        object.__setattr__(self, "url", _required_text(self.url, "target snapshot url"))
        parsed = urlparse(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            raise ValueError("target snapshot url must be a non-secret HTTP endpoint without userinfo/query/fragment")
        if self.http_method != "POST":
            raise ValueError("target snapshot only supports POST")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("target snapshot timeout_seconds must be positive")

    def as_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "http_method": self.http_method,
            "timeout_seconds": self.timeout_seconds,
            "url": self.url,
        }

    @classmethod
    def from_json(cls, value: Any) -> ExternalHttpTargetSnapshot:
        if not isinstance(value, dict) or frozenset(value) != _TARGET_SNAPSHOT_FIELDS:
            raise ValueError("target snapshot must contain only typed non-secret fields")
        return cls(
            code=value["code"],
            url=value["url"],
            http_method=value["http_method"],
            timeout_seconds=value["timeout_seconds"],
        )


@dataclass(frozen=True, slots=True)
class FrozenExternalHttpBinding:
    """SystemOutbox 持久化的完整非秘密 binding 快照。"""

    provider_profile_identity: str
    provider_profile_hash: str
    operation_identity: str
    binding_revision: str
    target_snapshot: ExternalHttpTargetSnapshot
    target_snapshot_hash: str
    auth_scheme: Literal["HMAC_SHA256"]
    credential_reference: str

    def __post_init__(self) -> None:
        _ = _required_text(self.provider_profile_identity, "provider_profile_identity")
        _ = _required_text(self.operation_identity, "operation_identity")
        if not _SHA256_RE.fullmatch(self.provider_profile_hash):
            raise ValueError("provider profile hash must be SHA-256")
        if not _SHA256_RE.fullmatch(self.binding_revision):
            raise ValueError("binding revision must be SHA-256")
        if not _SHA256_RE.fullmatch(self.target_snapshot_hash):
            raise ValueError("target snapshot hash must be SHA-256")
        if _sha256_json(self.target_snapshot.as_json()) != self.target_snapshot_hash:
            raise ValueError("target snapshot hash does not match frozen target snapshot")
        if self.auth_scheme not in _SUPPORTED_AUTH_SCHEMES:
            raise ValueError("unsupported frozen outbound auth scheme")
        if not _CREDENTIAL_REFERENCE_RE.fullmatch(self.credential_reference):
            raise ValueError("frozen binding requires a versioned credential reference")

    def as_persisted_fields(self) -> dict[str, Any]:
        return {
            "auth_scheme": self.auth_scheme,
            "binding_revision": self.binding_revision,
            "credential_reference": self.credential_reference,
            "operation_identity": self.operation_identity,
            "provider_profile_hash": self.provider_profile_hash,
            "provider_profile_identity": self.provider_profile_identity,
            "target_code": self.target_snapshot.code,
            "target_snapshot_hash": self.target_snapshot_hash,
            "target_snapshot_json": self.target_snapshot.as_json(),
        }

    @classmethod
    def from_persisted(
        cls,
        *,
        provider_profile_identity: object,
        provider_profile_hash: object,
        operation_identity: object,
        binding_revision: object,
        target_code: object,
        target_snapshot_json: Any,
        target_snapshot_hash: object,
        auth_scheme: object,
        credential_reference: object,
    ) -> FrozenExternalHttpBinding:
        target_snapshot = ExternalHttpTargetSnapshot.from_json(target_snapshot_json)
        persisted_target_code = require_string(target_code, "target_code")
        if target_snapshot.code != persisted_target_code:
            raise ValueError("persisted target code differs from target snapshot")
        return cls(
            provider_profile_identity=require_string(provider_profile_identity, "provider_profile_identity"),
            provider_profile_hash=require_string(provider_profile_hash, "provider_profile_hash"),
            operation_identity=require_string(operation_identity, "operation_identity"),
            binding_revision=require_string(binding_revision, "binding_revision"),
            target_snapshot=target_snapshot,
            target_snapshot_hash=require_string(target_snapshot_hash, "target_snapshot_hash"),
            auth_scheme=_persisted_auth_scheme(auth_scheme),
            credential_reference=require_string(credential_reference, "credential_reference"),
        )


def freeze_external_http_binding(
    *,
    profile: ExternalHttpProviderProfileDefinition,
    operation_identity: str,
    target_code: str,
    endpoint_registry: EndpointRegistry,
) -> FrozenExternalHttpBinding:
    """从 author-time binding 与当前 endpoint revision 生成一次性冻结快照。"""

    binding = profile.resolve_binding(operation_identity)
    if target_code not in binding.allowed_target_codes:
        raise ValueError("EXTERNAL_HTTP endpoint is not registered in the authored binding")
    endpoint = endpoint_registry.resolve(target_code)
    target_snapshot = ExternalHttpTargetSnapshot(
        code=endpoint.code,
        url=endpoint.url,
        http_method=binding.http_method,
        timeout_seconds=binding.timeout_seconds,
    )
    if profile.environment == "production" and urlparse(target_snapshot.url).scheme != "https":
        raise ValueError("production EXTERNAL_HTTP endpoint requires HTTPS")
    binding_revision = _sha256_json(
        {
            "binding": binding.canonical_payload(),
            "profile_hash": profile.profile_hash,
        }
    )
    return FrozenExternalHttpBinding(
        provider_profile_identity=profile.identity,
        provider_profile_hash=profile.profile_hash,
        operation_identity=binding.operation_identity,
        binding_revision=binding_revision,
        target_snapshot=target_snapshot,
        target_snapshot_hash=_sha256_json(target_snapshot.as_json()),
        auth_scheme=binding.auth_scheme,
        credential_reference=binding.credential_reference,
    )


__all__ = [
    "ExternalHttpBindingDefinition",
    "ExternalHttpProviderProfileDefinition",
    "ExternalHttpTargetSnapshot",
    "FrozenExternalHttpBinding",
    "freeze_external_http_binding",
]
