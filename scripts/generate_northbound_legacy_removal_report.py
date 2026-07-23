#!/usr/bin/env python3
"""生成北向 WMS legacy removal remaining inventory 与零搜索报告。"""

from __future__ import annotations

import argparse
import ast
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
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
            findings.extend(
                {
                    "kind": "text",
                    "path": relative_path.as_posix(),
                    "line": line_number,
                    "token": token,
                }
                for token in (*FORBIDDEN_TEXT, *FORBIDDEN_DISPATCH_FIELDS)
                if token in line
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


def _remaining_inventory() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _build_report() -> dict[str, Any]:
    findings = _live_findings()
    inventory = _remaining_inventory()
    return {
        "schema_version": "northbound-legacy-removal-report.v1",
        "scan_roots": [path.relative_to(ROOT).as_posix() for path in SCAN_ROOTS],
        "excluded_guard_paths": sorted(path.as_posix() for path in EXCLUDED_PATHS),
        "summary": {
            "finding_count": len(findings),
            "remaining_inventory_count": len(inventory),
            "findings_by_token": dict(sorted(Counter(str(item["token"]) for item in findings).items())),
        },
        "remaining_inventory": inventory,
        "findings": findings,
    }


def _render_report() -> str:
    return json.dumps(_build_report(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查已生成报告是否与当前扫描一致")
    args = parser.parse_args()
    rendered = _render_report()
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != rendered:
            print("northbound legacy removal report is stale")
            return 1
        print("northbound legacy removal report is current")
        return 0
    REPORT.write_text(rendered, encoding="utf-8", newline="\n")
    report = _build_report()
    print(
        "northbound legacy removal report generated: "
        f"findings={report['summary']['finding_count']} "
        f"remaining_inventory={report['summary']['remaining_inventory_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
