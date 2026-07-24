"""WMS staging conformance 的部署 trust root 配置边界。"""

from __future__ import annotations

import json
import os
import re
from base64 import urlsafe_b64decode
from binascii import Error as Base64DecodeError
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

if TYPE_CHECKING:
    from collections.abc import Mapping

WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV = "WMS_STAGING_CONFORMANCE_TRUST_ROOTS"
_SIGNING_KEY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,79}$")


@dataclass(frozen=True, slots=True)
class StagingConformanceTrustRootRegistry:
    """部署启动时固定的 Ed25519 公钥集合；runner 不接受调用方替换。"""

    _trusted_public_keys: Mapping[str, Ed25519PublicKey]

    @classmethod
    def from_public_keys(cls, trusted_public_keys: Mapping[str, bytes]) -> StagingConformanceTrustRootRegistry:
        parsed: dict[str, Ed25519PublicKey] = {}
        for key_id, public_key_bytes in trusted_public_keys.items():
            if not isinstance(key_id, str) or _SIGNING_KEY_ID_PATTERN.fullmatch(key_id) is None:
                raise ValueError("staging conformance trust root contains an invalid signing key identity")
            if not isinstance(public_key_bytes, bytes):
                raise TypeError("staging conformance trust root contains an invalid Ed25519 public key")
            try:
                parsed[key_id] = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            except ValueError as exc:
                raise ValueError("staging conformance trust root contains an invalid Ed25519 public key") from exc
        return cls(_trusted_public_keys=MappingProxyType(parsed))

    @property
    def signing_key_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._trusted_public_keys))

    def verify_signature(self, *, signing_key_id: str, payload: bytes, signature: bytes) -> None:
        public_key = self._trusted_public_keys.get(signing_key_id)
        if public_key is None:
            raise ValueError("staging attestation does not use a trusted signing key")
        try:
            public_key.verify(signature, payload)
        except (InvalidSignature, ValueError) as exc:
            raise ValueError("staging conformance executor attestation signature mismatch") from exc


def load_staging_conformance_trust_roots_from_environment() -> StagingConformanceTrustRootRegistry:
    """从部署环境读取公开 trust root；缺失时保持空集合并 fail closed。"""

    raw = os.environ.get(WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV)
    if raw is None or not raw.strip():
        return StagingConformanceTrustRootRegistry.from_public_keys({})
    try:
        encoded_public_keys = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("WMS staging conformance trust roots must be a JSON object") from exc
    if not isinstance(encoded_public_keys, dict) or not all(
        isinstance(key_id, str) and isinstance(encoded_key, str) for key_id, encoded_key in encoded_public_keys.items()
    ):
        raise ValueError("WMS staging conformance trust roots must map key identities to public keys")

    decoded_public_keys: dict[str, bytes] = {}
    for key_id, encoded_key in encoded_public_keys.items():
        try:
            decoded_public_keys[key_id] = urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
        except (Base64DecodeError, ValueError) as exc:
            raise ValueError("WMS staging conformance trust root contains invalid base64url") from exc
    return StagingConformanceTrustRootRegistry.from_public_keys(decoded_public_keys)


WMS_STAGING_CONFORMANCE_TRUST_ROOTS = load_staging_conformance_trust_roots_from_environment()


__all__ = [
    "WMS_STAGING_CONFORMANCE_TRUST_ROOTS",
    "WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV",
    "StagingConformanceTrustRootRegistry",
    "load_staging_conformance_trust_roots_from_environment",
]
