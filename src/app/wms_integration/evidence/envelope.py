"""Typed ExternalReference and EvidenceEnvelope models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExternalReference(BaseModel):
    """Typed external object reference."""

    model_config = ConfigDict(extra="forbid")

    system: str = Field(min_length=1, max_length=80)
    object_type: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=160)
    schema_version: str = Field(min_length=1, max_length=80)
    validated_at: str = Field(min_length=1)
    source_version: str = Field(min_length=1, max_length=80)


class EvidenceEnvelope(BaseModel):
    """Typed evidence envelope for external/runtime facts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1, max_length=80)
    source_system: str = Field(min_length=1, max_length=80)
    source_event_id: str = Field(min_length=1, max_length=160)
    source_version: str = Field(min_length=1, max_length=80)
    evidence_type: str = Field(min_length=1, max_length=120)
    occurred_at: str = Field(min_length=1)
    external_refs: list[ExternalReference]
    request_hash: str = Field(min_length=64, max_length=64)
    payload_hash: str = Field(min_length=64, max_length=64)
    payload: dict[str, object]
    legacy_payload: dict[str, object] | None = None


__all__ = ["EvidenceEnvelope", "ExternalReference"]
