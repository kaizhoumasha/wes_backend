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
