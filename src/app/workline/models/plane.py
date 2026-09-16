"""Plane scene/snapshot read models."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # Pydantic runtime validation 需要具体类型
from enum import Enum
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


class SceneBindingState(str, Enum):
    """Scene v2 资源行的静态绑定状态；不表达动态过程状态。"""

    BOUND = "BOUND"
    UNBOUND = "UNBOUND"
    INVALID = "INVALID"


class PlaneResourceGroup(str, Enum):
    """Scene v2 资源分组；只有这两组，不引入插件私有分组。"""

    POSITION_SLOT = "POSITION_SLOT"
    DEVICE_ROLE = "DEVICE_ROLE"


class PlaneResourceBinding(BaseModel):
    """Scene v2 资源行的实际绑定；只暴露编码、名称和启用状态。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    type: str | None = Field(default=None, max_length=40)
    enabled: bool = True


class PlaneResource(BaseModel):
    """Scene v2 单个资源行：Definition 声明 + WorkLine 实际绑定 + 绑定状态。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    stable_order: int = Field(ge=0)
    declared_constraints: dict[str, str | None] = Field(default_factory=dict)
    binding: PlaneResourceBinding | None = None
    binding_state: SceneBindingState


class PlaneOrphanBinding(BaseModel):
    """Definition 已不再声明、但历史 config 仍保留的绑定诊断。"""

    model_config = ConfigDict(extra="forbid")

    group: PlaneResourceGroup
    key: str = Field(min_length=1, max_length=100)
    bound_code: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=80)


class PlaneSceneDiagnostics(BaseModel):
    """Scene v2 附带诊断；不创建伪造资源行。"""

    model_config = ConfigDict(extra="forbid")

    orphan_bindings: list[PlaneOrphanBinding] = Field(default_factory=list)


class PlaneResourceGroups(BaseModel):
    """按 Definition 资源类型分组，顺序即展示顺序。"""

    model_config = ConfigDict(extra="forbid")

    POSITION_SLOT: list[PlaneResource] = Field(default_factory=list)
    DEVICE_ROLE: list[PlaneResource] = Field(default_factory=list)


class PlaneWorkLineIdentityV2(BaseModel):
    """Scene v2 的 WorkLine 静态身份切片；不包含 config 等敏感字段。"""

    model_config = ConfigDict(extra="forbid")

    id: int
    version: int
    line_code: str = Field(min_length=1, max_length=80)
    line_name: str = Field(min_length=1, max_length=120)
    line_type: str
    is_active: bool
    run_mode: str
    plugin_key: str | None = None
    plugin_version: str | None = None
    plugin_display_name: str | None = None


class PlaneSceneGeneratedFrom(BaseModel):
    """Scene v2 的重新生成触发依据；用于排查 revision 变化原因。"""

    model_config = ConfigDict(extra="forbid")

    workline_version: int
    plugin_version: str | None = None


class PlaneSceneV2(BaseModel):
    """资源中心的 WorkLine plane 静态 scene；与 plane.scene.v1 并存，互不改变语义。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["plane.scene.v2"]
    scene_revision: str = Field(min_length=1, max_length=64)
    workline: PlaneWorkLineIdentityV2
    generated_from: PlaneSceneGeneratedFrom
    resource_groups: PlaneResourceGroups
    diagnostics: PlaneSceneDiagnostics


class PlaneResourceRef(BaseModel):
    """Snapshot/Active Objects 关联 Scene 资源行的唯一引用；不表达绑定详情。"""

    model_config = ConfigDict(extra="forbid")

    group: PlaneResourceGroup
    key: str = Field(min_length=1, max_length=100)


class PlaneSnapshotSourceStatus(str, Enum):
    """Snapshot v2 数据来源状态；非 COMPLETE 时前端不得渲染伪造的零活动。"""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    STALE = "STALE"


class PlaneResourceState(BaseModel):
    """单个资源在当前 Snapshot 下的活动摘要；只在 source_status=COMPLETE 时可信。"""

    model_config = ConfigDict(extra="forbid")

    resource_ref: PlaneResourceRef
    active_object_count: int = Field(ge=0)
    highest_conflict_state: Literal["OK", "TRANSIENT", "RECONCILING"]


class PlaneSnapshotV2(BaseModel):
    """资源中心的 WorkLine plane 动态 snapshot；与 plane.snapshot.v1 并存，互不改变语义。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["plane.snapshot.v2"]
    scene_revision: str | None = Field(default=None, max_length=64)
    generated_at: datetime | None = None
    source_status: PlaneSnapshotSourceStatus
    truncated: bool = False
    total_count: int = Field(default=0, ge=0)
    resource_states: list[PlaneResourceState] = Field(default_factory=list)
    unmapped_object_count: int = Field(default=0, ge=0)


class PlaneActiveObjectLocation(BaseModel):
    """Active Objects v2 的位置证据摘要；与 v1 同源，仅冲突状态改为字面量。"""

    model_config = ConfigDict(extra="forbid")

    location_scope: str
    location_code: str
    conflict_state: Literal["OK", "TRANSIENT", "RECONCILING"]
    evidence_refs: list[str] = Field(default_factory=list)


class PlaneActiveObjectView(BaseModel):
    """Active Objects v2 单个对象视图；在 v1 字段基础上追加 resource_ref。"""

    model_config = ConfigDict(extra="forbid")

    object_type: str
    object_key: str
    conflict_state: Literal["OK", "TRANSIENT", "RECONCILING"]
    primary_source: str | None = None
    all_sources: list[str] = Field(default_factory=list)
    operator_hint: str | None = None
    location_summary: PlaneActiveObjectLocation | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    resource_ref: PlaneResourceRef | None = None


class PlaneActiveObjectsV2(BaseModel):
    """资源中心的 WorkLine active objects；与 v1 并存，互不改变语义。"""

    model_config = ConfigDict(extra="forbid")

    workline_id: int
    scene_revision: str | None = Field(default=None, max_length=64)
    objects: list[PlaneActiveObjectView] = Field(default_factory=list)
    truncated: bool = False
    total_count: int = Field(default=0, ge=0)


__all__ = [
    "PlaneActiveObjectLocation",
    "PlaneActiveObjectView",
    "PlaneActiveObjectsV2",
    "PlaneEdge",
    "PlaneExtremeState",
    "PlaneNode",
    "PlaneObjectSnapshot",
    "PlaneOrphanBinding",
    "PlaneResource",
    "PlaneResourceBinding",
    "PlaneResourceGroup",
    "PlaneResourceGroups",
    "PlaneResourceRef",
    "PlaneResourceState",
    "PlaneSceneDiagnostics",
    "PlaneSceneGeneratedFrom",
    "PlaneSceneV2",
    "PlaneSceneView",
    "PlaneSnapshot",
    "PlaneSnapshotSourceStatus",
    "PlaneSnapshotV2",
    "PlaneWorkLineIdentityV2",
    "SceneBindingState",
]
