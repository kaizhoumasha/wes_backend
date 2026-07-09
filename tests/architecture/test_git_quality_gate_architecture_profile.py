"""本地 quality gate 必须执行 architecture guardrails。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QUALITY_GATE = REPO_ROOT / "scripts" / "git-quality-gate.sh"


def test_quality_profile_runs_architecture_check(tmp_path):
    """quality profile 必须真实执行 architecture guardrails，而不是只保留字符串。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "ARCHITECTURE_GUARDRAIL_MODE": "invalid-mode-for-test",
    }
    result = subprocess.run(
        ["/bin/bash", str(QUALITY_GATE), "--profile", "quality"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "[architecture] architecture-guardrails.sh --mode invalid-mode-for-test" in result.stdout
    assert "未知 mode: invalid-mode-for-test" in result.stderr


def test_quality_profile_runs_runtime_toggle_release_gate(tmp_path):
    """quality profile 必须调用 runtime toggle 发布门禁。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"scripts/check_runtime_toggle_release_gate.py"* ]]; then
  echo "runtime toggle release gate reached" >&2
  exit 23
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["/bin/bash", str(QUALITY_GATE), "--profile", "quality"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 23
    assert "[runtime-toggle] check_runtime_toggle_release_gate.py" in result.stdout
    assert "runtime toggle release gate reached" in result.stderr


def test_quality_profile_runs_runtime_evidence_readiness_gate(tmp_path):
    """quality profile 必须调用 runtime evidence readiness 门禁。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"scripts/check_runtime_evidence_readiness_gate.py"* ]]; then
  echo "runtime evidence readiness gate reached" >&2
  exit 24
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["/bin/bash", str(QUALITY_GATE), "--profile", "quality"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 24
    assert "[runtime-evidence-readiness] check_runtime_evidence_readiness_gate.py" in result.stdout
    assert "runtime evidence readiness gate reached" in result.stderr


def test_quality_gate_no_longer_exposes_workline_restructuring_readiness() -> None:
    """quality gate 不再暴露已退役的 WorkLine restructuring readiness check。"""
    text = QUALITY_GATE.read_text(encoding="utf-8")
    retired_check = "workline-" + "restructuring-readiness"
    retired_script = "check_workline_" + "restructuring_readiness_gate.py"

    assert retired_check not in text
    assert retired_script not in text


def test_quality_profile_runs_runtime_production_closure_gate(tmp_path):
    """quality profile 必须调用 runtime production closure 门禁。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"scripts/check_runtime_production_closure_gate.py"* ]]; then
  echo "runtime production closure gate reached" >&2
  exit 26
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["/bin/bash", str(QUALITY_GATE), "--profile", "quality"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 26
    assert "[runtime-production-closure] check_runtime_production_closure_gate.py" in result.stdout
    assert "runtime production closure gate reached" in result.stderr


def test_quality_profile_runs_process_naming_guardrail(tmp_path):
    """quality profile 必须调用 active process naming 守卫。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"tests/architecture/test_process_naming_guardrail.py"* ]]; then
  echo "process naming guardrail reached" >&2
  exit 25
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["/bin/bash", str(QUALITY_GATE), "--profile", "quality"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 25
    assert "[process-naming] pytest tests/architecture/test_process_naming_guardrail.py -q" in result.stdout
    assert "process naming guardrail reached" in result.stderr


def test_quality_gate_falls_back_to_script_root_without_git_metadata(tmp_path):
    """CI 镜像无 .git 元数据时，quality gate 仍应从脚本位置定位仓库。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)

    fake_git = fake_bin / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_git.chmod(0o755)

    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"scripts/check_runtime_toggle_release_gate.py"* ]]; then
  echo "runtime toggle release gate reached" >&2
  exit 23
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["/bin/bash", str(QUALITY_GATE), "--check", "runtime-toggle-release"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 23
    assert "[runtime-toggle] check_runtime_toggle_release_gate.py" in result.stdout
    assert "runtime toggle release gate reached" in result.stderr
