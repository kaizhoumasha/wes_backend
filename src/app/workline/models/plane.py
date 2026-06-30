"""Plane scene/snapshot read models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlaneNode(BaseModel):
    """Plane scene node with stable code and display label."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=80)


class PlaneEdge(BaseModel):
    """Plane scene edge."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    from_code: str = Field(min_length=1, max_length=120)
    to_code: str = Field(min_length=1, max_length=120)
    label: str | None = Field(default=None, max_length=120)


class PlaneSceneView(BaseModel):
    """WorkLine plane static scene view."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["plane.scene.v1"]
    workline_code: str = Field(min_length=1, max_length=80)
    nodes: list[PlaneNode]
    edges: list[PlaneEdge]


class PlaneObjectSnapshot(BaseModel):
    """Plane snapshot object state."""

    model_config = ConfigDict(extra="forbid")

    object_code: str = Field(min_length=1, max_length=120)
    object_label: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=80)


class PlaneExtremeState(BaseModel):
    """Plane snapshot extreme state marker."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    severity: str = Field(min_length=1, max_length=40)


class PlaneSnapshot(BaseModel):
    """WorkLine plane dynamic snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["plane.snapshot.v1"]
    workline_code: str = Field(min_length=1, max_length=80)
    scene_schema_version: Literal["plane.scene.v1"]
    objects: list[PlaneObjectSnapshot]
    extremes: list[PlaneExtremeState]


__all__ = [
    "PlaneEdge",
    "PlaneExtremeState",
    "PlaneNode",
    "PlaneObjectSnapshot",
    "PlaneSceneView",
    "PlaneSnapshot",
]
