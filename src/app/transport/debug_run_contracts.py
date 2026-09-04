"""Transport 自动联调轮次的稳定内部合同。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TransportDebugRunStatus(StrEnum):
    RUNNING = "RUNNING"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class TransportDebugRunPhase(StrEnum):
    RACK_TO_STATION = "RACK_TO_STATION"
    BINS_TO_INFEED = "BINS_TO_INFEED"
    WAIT_SCAN12 = "WAIT_SCAN12"
    BINS_TO_RACK = "BINS_TO_RACK"
    ROTATE_TO_NEXT_FACE = "ROTATE_TO_NEXT_FACE"
    RACK_TO_STORAGE = "RACK_TO_STORAGE"


class TransportDebugRunStepStatus(StrEnum):
    PENDING = "PENDING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


@dataclass(frozen=True, slots=True)
class TransportDebugBinSelection:
    bin_id: str
    slot_id: str

    def __post_init__(self) -> None:
        if not self.bin_id.strip():
            raise ValueError("料箱编码不能为空")
        if not self.slot_id.strip():
            raise ValueError("原货架槽位不能为空")


@dataclass(frozen=True, slots=True)
class TransportDebugFaceGroup:
    face: str
    bins: tuple[TransportDebugBinSelection, ...]

    def __post_init__(self) -> None:
        if not self.face.strip():
            raise ValueError("面值不能为空")
        if not 1 <= len(self.bins) <= 4:
            raise ValueError("每个面必须选择 1～4 个料箱")


@dataclass(frozen=True, slots=True)
class CreateTransportDebugRun:
    rack_id: str
    face_groups: tuple[TransportDebugFaceGroup, ...]

    def __post_init__(self) -> None:
        if not self.rack_id.strip():
            raise ValueError("货架编码不能为空")
        if not self.face_groups:
            raise ValueError("至少选择一个货架面")

        faces: set[str] = set()
        bin_ids: set[str] = set()
        for group in self.face_groups:
            if group.face in faces:
                raise ValueError(f"重复面值: {group.face}")
            faces.add(group.face)
            for selection in group.bins:
                if selection.bin_id in bin_ids:
                    raise ValueError(f"重复料箱: {selection.bin_id}")
                bin_ids.add(selection.bin_id)


__all__ = [
    "CreateTransportDebugRun",
    "TransportDebugBinSelection",
    "TransportDebugFaceGroup",
    "TransportDebugRunPhase",
    "TransportDebugRunStatus",
    "TransportDebugRunStepStatus",
]
