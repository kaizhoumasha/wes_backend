"""SMT 分拣入库的 config、state、route input 与 facts。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from src.app.runtime.workline_plugins.dispatcher import PinnedPluginSnapshot  # noqa: TC001

StableString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class SmtSortingInboundConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_profile: StableString
    source_arm_role: Literal["SORTING_SOURCE_ARM"] = "SORTING_SOURCE_ARM"


class SmtSortingInboundState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: Literal["WAITING_SOURCE_PICK", "WAITING_SCAN"] = "WAITING_SOURCE_PICK"
    current_correlation: StableString | None = None


class SourcePickRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["SOURCE_PICK_REQUESTED"] = "SOURCE_PICK_REQUESTED"
    command_code: StableString


class SmtSortingInboundFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_matches: bool = True
    route_diagnostic: StableString | None = None
    binding_snapshot: PinnedPluginSnapshot


__all__ = [
    "SmtSortingInboundConfig",
    "SmtSortingInboundFacts",
    "SmtSortingInboundState",
    "SourcePickRequestInput",
]
