"""EXECUTION_CORRELATION_BOUNDARY guardrail: 跨域 session FK 收敛为 ExecutionCorrelation。

验证 architecture-guardrails.sh EXECUTION_CORRELATION_BOUNDARY 规则识别跨域 session_id。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.workline_inbox_retirement_guardrail import CURRENT_DOC_FILES

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
OPERATION_API = REPO_ROOT / "src/app/workline/v1/operation.py"


def _run_guardrail_fixture(tmp_path: Path, operation_line: str) -> subprocess.CompletedProcess[str]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    fixture_guardrail = scripts_dir / "architecture-guardrails.sh"
    fixture_guardrail.write_text(GUARDRAIL.read_text())
    shutil.copy(
        REPO_ROOT / "scripts" / "workline_inbox_retirement_guardrail.py",
        scripts_dir / "workline_inbox_retirement_guardrail.py",
    )
    for relative_path in CURRENT_DOC_FILES:
        source = REPO_ROOT / relative_path
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
    allowlist = scripts_dir / "architecture-guardrails.allowlist"
    allowlist.write_text("")
    operation = tmp_path / "src/app/workline/v1/operation.py"
    operation.parent.mkdir(parents=True)
    operation.write_text(f"def response(inbox):\n    return {{\n{operation_line}    }}\n")
    return subprocess.run(
        ["/bin/bash", str(fixture_guardrail), "--mode", "enforced", "--allowlist", str(allowlist)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )


def test_execution_correlation_boundary_fixture_triggers_violation():
    """违反 EXECUTION_CORRELATION_BOUNDARY 的跨域 session FK 应被识别。"""
    violation_field = "workline_session_id: int | None"
    assert "workline_session_id" in violation_field


def test_execution_correlation_boundary_rule_exists_in_script():
    content = GUARDRAIL.read_text()
    assert "rule_execution_correlation_boundary" in content
    assert "workline_session_id" in content


def test_runtime_inbox_response_mapping_has_only_a_narrow_canonical_exception():
    """API 对外 session_id 允许直接映射 canonical 字段，但不得放宽旧字段双读。"""

    guardrail_content = GUARDRAIL.read_text()
    operation_content = OPERATION_API.read_text()
    assert "runtime_inbox_response_mapping=" in guardrail_content
    assert "inbox\\.workline_session_id" in guardrail_content
    assert '"$_content" =~ $runtime_inbox_response_mapping' in guardrail_content
    assert '"session_id": inbox.workline_session_id,' in operation_content
    assert 'getattr(inbox, "session_id"' not in operation_content


def test_canonical_runtime_inbox_response_mapping_passes_guardrail(tmp_path: Path):
    result = _run_guardrail_fixture(tmp_path, '        "session_id"  :  inbox.workline_session_id,\n')
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "extra_reference",
    [
        '"material": material_session_id',
        '"legacy": legacy.workline_session_id',
    ],
)
def test_canonical_mapping_cannot_hide_another_forbidden_reference(
    tmp_path: Path,
    extra_reference: str,
):
    line = f'        "session_id": inbox.workline_session_id, {extra_reference},\n'
    result = _run_guardrail_fixture(tmp_path, line)
    assert result.returncode != 0
    assert "EXECUTION_CORRELATION_BOUNDARY violation" in result.stderr
