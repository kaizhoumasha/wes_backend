"""Workline Plugin / System Capability 扩展平台架构门禁。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"


def _run_fixture(tmp_path: Path, relative_path: str, source: str) -> subprocess.CompletedProcess[str]:
    fixture_file = tmp_path / relative_path
    fixture_file.parent.mkdir(parents=True, exist_ok=True)
    fixture_file.write_text(source, encoding="utf-8")
    env = os.environ.copy()
    env["RUNTIME_EXTENSION_GUARDRAIL_FIXTURE_ROOT"] = str(tmp_path)
    env["RUNTIME_EXTENSION_GUARDRAIL_FIXTURE_ONLY"] = "1"
    return subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", "enforced", "--allowlist", str(ALLOWLIST)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("relative_path", "source", "rule_id"),
    [
        (
            "workline_plugins/example/handler.py",
            "from sqlalchemy import select\n",
            "WORKLINE_PLUGIN_DEPENDENCY_BOUNDARY",
        ),
        (
            "system_capabilities/example/handler.py",
            "await db.commit()\n",
            "SYSTEM_CAPABILITY_DEPENDENCY_BOUNDARY",
        ),
        (
            "workline_plugins/generated_index.py",
            "import importlib\nimportlib.import_module('example')\n",
            "RUNTIME_GENERATED_INDEX_STATICITY",
        ),
        (
            "orchestration/orchestrator_bridge.py",
            "if event_type == 'SCAN_COMPLETED':\n    pass\n",
            "RUNTIME_EXTENSION_GENERIC_ORCHESTRATION",
        ),
        (
            "consumer.py",
            "from src.app.runtime.capability_catalog import get_workline_capability_definition\n",
            "LEGACY_CAPABILITY_ROUTING_IMPORT",
        ),
    ],
)
def test_runtime_extension_guardrail_reports_rule_file_and_line(
    tmp_path: Path,
    relative_path: str,
    source: str,
    rule_id: str,
) -> None:
    result = _run_fixture(tmp_path, relative_path, source)

    assert result.returncode == 1
    assert f"[{rule_id}]" in result.stderr
    assert f"file: {tmp_path / relative_path}:1" in result.stderr


def test_runtime_extension_platform_has_no_unallowlisted_violation() -> None:
    result = subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", "enforced", "--allowlist", str(ALLOWLIST)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_new_extension_platform_directories_cannot_be_allowlisted() -> None:
    rows = [row for row in ALLOWLIST.read_text(encoding="utf-8").splitlines() if row and not row.startswith("#")]

    assert all("|src/app/runtime/workline_plugins/" not in row for row in rows)
    assert all("|src/app/runtime/system_capabilities/" not in row for row in rows)
