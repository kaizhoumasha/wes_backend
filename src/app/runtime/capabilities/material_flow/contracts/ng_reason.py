"""Material-flow unified NG reason taxonomy."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum


class NgReasonSource(str, Enum):
    """Source of a canonical NG reason."""

    DEVICE_ERROR = "DEVICE_ERROR"
    RUNTIME = "RUNTIME"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class NgReasonDefinition:
    """Canonical NG reason exposed to Runtime Hold release."""

    canonical_code: str
    label: str
    source: NgReasonSource
    maps_from: tuple[str, ...] = ()
    deprecated: bool = False

    def __post_init__(self) -> None:
        if not self.canonical_code:
            raise ValueError("NG reason canonical_code must not be empty")
        if not self.label:
            raise ValueError("NG reason label must not be empty")


@dataclass(frozen=True)
class NgReasonCatalog:
    """Normalized NG reason lookup tables."""

    reasons: tuple[NgReasonDefinition, ...]
    by_code: dict[str, NgReasonDefinition]
    by_source: dict[NgReasonSource, tuple[NgReasonDefinition, ...]]


BUILTIN_NG_REASONS: tuple[NgReasonDefinition, ...] = (
    NgReasonDefinition(
        canonical_code="UNKNOWN_PHYSICAL_STATE",
        label="设备动作状态未知",
        source=NgReasonSource.RUNTIME,
        maps_from=("COMMAND_ACK_EXHAUSTED", "CALLBACK_DEADLINE_EXPIRED"),
    ),
    NgReasonDefinition(
        canonical_code="OPERATOR_JUDGED_NG",
        label="现场人工判定 NG",
        source=NgReasonSource.MANUAL,
    ),
    NgReasonDefinition(
        canonical_code="RUNTIME_RECOVERY_NG",
        label="运行时异常恢复转 NG",
        source=NgReasonSource.RUNTIME,
    ),
)


def build_ng_reason_catalog() -> NgReasonCatalog:
    """Build the built-in NG reason catalog."""

    reasons = BUILTIN_NG_REASONS
    by_code: dict[str, NgReasonDefinition] = {}
    grouped: dict[NgReasonSource, list[NgReasonDefinition]] = defaultdict(list)
    for reason in reasons:
        if reason.canonical_code in by_code:
            raise ValueError(f"duplicate NG reason canonical_code: {reason.canonical_code}")
        by_code[reason.canonical_code] = reason
        grouped[reason.source].append(reason)

    return NgReasonCatalog(
        reasons=reasons,
        by_code=by_code,
        by_source={source: tuple(items) for source, items in grouped.items()},
    )


__all__ = [
    "BUILTIN_NG_REASONS",
    "NgReasonCatalog",
    "NgReasonDefinition",
    "NgReasonSource",
    "build_ng_reason_catalog",
]
