"""粗分机插件最小局部编排状态。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

OptionalReference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)] | None
RoughSorterPhase = Literal["READY", "PICK_TO_PIPELINE", "MOVING_FORWARD", "NG_MOVING", "COMPLETED"]


class RoughSorterState(BaseModel):
    """不复制 MaterialUnit、DeviceCommand 或六合一码权威事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: RoughSorterPhase = "READY"
    measurement_evidence_ref: OptionalReference = None
    wms_evidence_ref: OptionalReference = None
    current_correlation: OptionalReference = None


__all__ = ["RoughSorterPhase", "RoughSorterState"]
