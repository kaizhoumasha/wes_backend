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
        "ARCHITECTURE_PHASE": "invalid-phase-for-test",
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
    assert "[architecture] architecture-guardrails.sh --phase invalid-phase-for-test" in result.stdout
    assert "未知 phase: invalid-phase-for-test" in result.stderr


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


def test_quality_profile_runs_phase4_runtime_readiness_gate(tmp_path):
    """quality profile 必须调用 Phase4 runtime readiness 门禁。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"scripts/check_phase4_runtime_readiness_gate.py"* ]]; then
  echo "phase4 runtime readiness gate reached" >&2
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
    assert "[phase4-readiness] check_phase4_runtime_readiness_gate.py" in result.stdout
    assert "phase4 runtime readiness gate reached" in result.stderr


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
