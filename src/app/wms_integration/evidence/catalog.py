"""Typed ExternalReference catalog and drift classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.wms_integration.evidence.envelope import ExternalReference


class ExternalReferenceDriftKind(str, Enum):
    """External reference drift classification."""

    NONE = "NONE"
    UNKNOWN_REFERENCE_TYPE = "UNKNOWN_REFERENCE_TYPE"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"


@dataclass(frozen=True, slots=True)
class ExternalReferenceCatalogEntry:
    """Allowed external reference type."""

    system: str
    object_type: str
    schema_version: str
    source_version: str


@dataclass(frozen=True, slots=True)
class ExternalReferenceDrift:
    """External reference validation result."""

    kind: ExternalReferenceDriftKind
    reference: ExternalReference
    expected_source_version: str | None = None


class ExternalReferenceCatalog:
    """Validate typed ExternalReference values against a provider catalog."""

    def __init__(self, entries: list[ExternalReferenceCatalogEntry]) -> None:
        self._entries = {(entry.system, entry.object_type, entry.schema_version): entry for entry in entries}

    def classify(self, reference: ExternalReference) -> ExternalReferenceDrift:
        entry = self._entries.get((reference.system, reference.object_type, reference.schema_version))
        if entry is None:
            return ExternalReferenceDrift(ExternalReferenceDriftKind.UNKNOWN_REFERENCE_TYPE, reference)
        if entry.source_version != reference.source_version:
            return ExternalReferenceDrift(
                ExternalReferenceDriftKind.SOURCE_VERSION_MISMATCH,
                reference,
                expected_source_version=entry.source_version,
            )
        return ExternalReferenceDrift(ExternalReferenceDriftKind.NONE, reference)

    def classify_all(self, references: list[ExternalReference]) -> list[ExternalReferenceDrift]:
        return [self.classify(reference) for reference in references]


__all__ = [
    "ExternalReferenceCatalog",
    "ExternalReferenceCatalogEntry",
    "ExternalReferenceDrift",
    "ExternalReferenceDriftKind",
]
