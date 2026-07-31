"""粗分机 attempt 使用的不可变 binding 摘要。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

StableString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StableHash = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


class RoughSorterBindingSnapshot(BaseModel):
    """插件决策可重放所需的不可变 binding 摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: int = Field(gt=0)
    binding_version: int = Field(gt=0)
    profile_identity: StableString
    plugin_config_hash: StableHash
    generated_index_digest: StableHash


__all__ = [
    "RoughSorterBindingSnapshot",
]
