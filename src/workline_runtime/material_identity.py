"""Plugin-owned material identity contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MaterialIdentityResolutionStatus(str, Enum):
    """Material identity resolution result."""

    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    MISSING = "MISSING"


@dataclass(frozen=True)
class MaterialIdentityInput:
    """Evidence available to a plugin material identity resolver."""

    session_context: Mapping[str, Any] | None = None
    source_payload: Mapping[str, Any] | None = None
    command_payload: Mapping[str, Any] | None = None
    material_scan_payload: Mapping[str, Any] | None = None
    plugin_context: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class MaterialIdentity:
    """Material identity resolved by a plugin.

    Display fields are operator-facing only. WES uses `idempotency_key` for
    dedupe and NG return ownership.
    """

    resolution_status: MaterialIdentityResolutionStatus
    idempotency_key: str | None = None
    business_key: str | None = None
    display: dict[str, Any] = field(default_factory=dict)
    raw_evidence_hash: str | None = None

    def __post_init__(self) -> None:
        if self.resolution_status == MaterialIdentityResolutionStatus.RESOLVED and not self.idempotency_key:
            raise ValueError("resolved material identity requires idempotency_key")
        if self.resolution_status != MaterialIdentityResolutionStatus.RESOLVED and self.idempotency_key is not None:
            raise ValueError("unresolved material identity must not carry idempotency_key")


MaterialIdentityResolver = Callable[[MaterialIdentityInput], MaterialIdentity]


def hash_material_evidence(value: Any) -> str:
    """Hash material evidence using canonical JSON."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def material_identity_input_to_hash(input_value: MaterialIdentityInput) -> str:
    """Hash resolver input without requiring callers to know dataclass details."""

    return hash_material_evidence(asdict(input_value))


__all__ = [
    "MaterialIdentity",
    "MaterialIdentityInput",
    "MaterialIdentityResolutionStatus",
    "MaterialIdentityResolver",
    "hash_material_evidence",
    "material_identity_input_to_hash",
]
