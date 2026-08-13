"""DeviceCommand 核心保持业务与供应商无关。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVICE_ROOT = REPO_ROOT / "src/app/device"


def test_device_core_has_no_business_or_supplier_coupling() -> None:
    forbidden = ("PickingTask", "GRN", "ROUGH_SORT", "SMT", "FANUC", "KEYENCE")
    violations: list[str] = []
    for path in DEVICE_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in content:
                violations.append(f"{path.relative_to(REPO_ROOT)}: {token}")
    assert violations == []


def test_device_core_uses_only_shared_outbound_transport() -> None:
    violations: list[str] = []
    for path in DEVICE_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "httpx.AsyncClient" in content or "import httpx" in content:
            violations.append(str(path.relative_to(REPO_ROOT)))
    assert violations == []
