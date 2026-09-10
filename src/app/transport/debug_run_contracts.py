"""Transport 自动联调轮次的稳定内部合同。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from wes_plugin_sdk.validation import validate_opaque_face


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
    bin_code: str
    slot_id: str

    def __post_init__(self) -> None:
        if not self.bin_code.strip():
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
        validate_opaque_face(self.face, "face")
        if not 1 <= len(self.bins) <= 4:
            raise ValueError("每个面必须选择 1～4 个料箱")


@dataclass(frozen=True, slots=True)
class CreateTransportDebugRun:
    workline_code: str
    rack_id: str
    face_groups: tuple[TransportDebugFaceGroup, ...]

    workstation: str = "KT16"
    infeed_position: str = "CNV0301"
    outfeed_position: str = "CNV0302"
    scan_device_codes: tuple[str, ...] = ("STATION_SCAN9", "STATION_SCAN10", "STATION_SCAN11", "STATION_SCAN12")

    def __post_init__(self) -> None:
        for value in (self.workstation, self.infeed_position, self.outfeed_position, *self.scan_device_codes):
            if not value.strip() or value != value.strip() or "\x00" in value or len(value) > 100:
                raise ValueError("点位和设备编码必须为 1～100 字符且不含首尾空白")
        if len(self.scan_device_codes) != 4 or len(set(self.scan_device_codes)) != 4:
            raise ValueError("必须配置四个不同的扫码设备")
        if self.infeed_position == self.outfeed_position:
            raise ValueError("投料口和出料口不能相同")

        if not self.workline_code.strip():
            raise ValueError("工作线编码不能为空")
        if not self.rack_id.strip():
            raise ValueError("货架编码不能为空")
        if not self.face_groups:
            raise ValueError("至少选择一个货架面")

        faces: set[str] = set()
        bin_codes: set[str] = set()
        for group in self.face_groups:
            if group.face in faces:
                raise ValueError(f"重复面值: {group.face}")
            faces.add(group.face)
            for selection in group.bins:
                if selection.bin_code in bin_codes:
                    raise ValueError(f"重复料箱: {selection.bin_code}")
                bin_codes.add(selection.bin_code)


__all__ = [
    "CreateTransportDebugRun",
    "TransportDebugBinSelection",
    "TransportDebugFaceGroup",
    "TransportDebugRunPhase",
    "TransportDebugRunStatus",
    "TransportDebugRunStepStatus",
]
