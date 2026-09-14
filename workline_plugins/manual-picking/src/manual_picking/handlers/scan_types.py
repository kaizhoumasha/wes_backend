"""四点扫码纯决定的最小输入与输出。"""

import re
from dataclasses import dataclass
from typing import Literal

ScanRoute = Literal["MOVE_FORWARD", "MOVE_RIGHT", "MOVE_LEFT", "REQUEST_WMS", "HOLD"]
_BIN_CODE = re.compile(r"A[0-9]{9}\Z")


@dataclass(frozen=True, slots=True)
class PassageSnapshot:
    bin_code: str | None
    ng: bool = False
    normal_authorized: bool = False
    scan3_forward: bool = False


@dataclass(frozen=True, slots=True)
class ScanFact:
    role: str
    evidence_id: int
    raw_bin_code: str | None
    passage: PassageSnapshot | None
    task_id: str | None


@dataclass(frozen=True, slots=True)
class ScanDecision:
    route: ScanRoute
    normal_bin_code: str | None = None
    ng_reason: str | None = None


def normal_bin_code(raw: str | None, suffix: str) -> str | None:
    if not isinstance(raw, str) or not raw.endswith(suffix):
        return None
    return scanned_bin_identity(raw)


def scanned_bin_identity(raw: str | None) -> str | None:
    if not isinstance(raw, str) or len(raw) < 3 or raw[-2:] not in {"-A", "-B", "-C", "-D"}:
        return None
    code = raw[:-2]
    return code if _BIN_CODE.fullmatch(code) else None
