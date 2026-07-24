"""北向 WMS legacy removal 的机器闭包守卫。"""

from __future__ import annotations

import ast
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts/generate_northbound_legacy_removal_report.py"
REPORT = ROOT / "docs/architecture/northbound-legacy-removal-report.json"
INVENTORY = ROOT / "docs/architecture/northbound-wms-operation-inventory.csv"

SCAN_ROOTS = (ROOT / "src", ROOT / "tests")
ACTIVE_TEXT_SUFFIXES = frozenset({".json", ".py", ".toml", ".yaml", ".yml"})
EXCLUDED_PATHS = {
    Path("tests/architecture/test_northbound_legacy_removal.py"),
    Path("tests/architecture/test_northbound_wms_operation_inventory.py"),
}
FORBIDDEN_TEXT = (
    "effect_contracts",
    "runtime_capabilities_query",
    "runtime_capabilities_effect",
    "port_requirements_json",
    "active_plugin_port_requirements_json",
    "WmsEndpointConfig",
    "WmsTypedPortService",
    "WMS_INVENTORY_TRANSACTION",
    "WmsFulfillmentPort.full_box_exchange",
    "Port.method",
    "port_method",
)
FORBIDDEN_DISPATCH_FIELDS = ("dispatch_status",)
EXTERNAL_HTTP_SENDER_NAMES = frozenset({"dispatch_external_http"})


def _active_text_files() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for root in SCAN_ROOTS
            for path in root.rglob("*")
            if path.is_file() and path.suffix in ACTIVE_TEXT_SUFFIXES and path.relative_to(ROOT) not in EXCLUDED_PATHS
        )
    )


def _active_python_files() -> tuple[Path, ...]:
    return tuple(path for path in _active_text_files() if path.suffix == ".py")


def _text_findings() -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in _active_text_files():
        relative_path = path.relative_to(ROOT)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for token in (*FORBIDDEN_TEXT, *FORBIDDEN_DISPATCH_FIELDS):
                if token in line:
                    findings.append(
                        {
                            "kind": "text",
                            "path": relative_path.as_posix(),
                            "line": line_number,
                            "token": token,
                        }
                    )
    return findings


def _boolean_sender_findings() -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in _active_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in EXTERNAL_HTTP_SENDER_NAMES or node.returns is None:
                continue
            annotation = ast.unparse(node.returns)
            if "bool" in annotation:
                findings.append(
                    {
                        "kind": "boolean_sender",
                        "path": path.relative_to(ROOT).as_posix(),
                        "line": node.lineno,
                        "token": f"{node.name} -> {annotation}",
                    }
                )
    return findings


def _live_findings() -> list[dict[str, object]]:
    return sorted(
        [*_text_findings(), *_boolean_sender_findings()],
        key=lambda item: (str(item["path"]), int(item["line"]), str(item["token"])),
    )


def test_zero_search_report_generator_and_snapshot_exist() -> None:
    assert GENERATOR.is_file(), "缺少 northbound legacy removal 唯一报告生成器"
    assert REPORT.is_file(), "缺少生成的 northbound legacy removal JSON 报告"


def test_generated_zero_search_report_matches_live_scan() -> None:
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    assert payload["findings"] == _live_findings()


def test_all_northbound_legacy_removal_targets_are_zero() -> None:
    assert _live_findings() == []


def test_operation_inventory_has_no_active_legacy_consumer() -> None:
    with INVENTORY.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows == []
