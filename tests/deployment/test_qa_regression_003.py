"""RuntimeInbox PostgreSQL acceptance 生命周期脚本回归测试。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_SCRIPT = REPO_ROOT / "scripts/run_runtime_inbox_postgresql_acceptance_ci.sh"


def test_acceptance_preflight_rejects_missing_commit_without_docker_side_effects(tmp_path: Path) -> None:
    """CI 元数据缺失时必须在创建隔离资源前失败。"""
    # Regression: ISSUE-001 — 复合命令中的未设置 CI_COMMIT_SHA 被 EXIT trap 改写为成功
    # Found by /qa on 2026-07-14
    # Historical report: project sibling ../archive_docs/wes_backend/local-artifacts/.gstack/
    # qa-reports/qa-report-wes-backend-docker-2026-07-14.md
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >>"${QA_DOCKER_LOG}"
if [[ "${1:-}" == "inspect" ]]; then
    printf 'healthy\n'
fi
""",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)

    environment = os.environ.copy()
    environment.pop("CI_COMMIT_SHA", None)
    environment.update(
        {
            "BUILD_NUMBER": "qa",
            "CI_SHORT_COMMIT": "regression",
            "CI_IMAGE": "unused:test",
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "QA_DOCKER_LOG": str(docker_log),
            "WORKSPACE": str(tmp_path),
        }
    )

    result = subprocess.run(
        ["/bin/bash", str(LIFECYCLE_SCRIPT), "run"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "CI_COMMIT_SHA is required" in result.stderr
    assert not docker_log.exists()
    assert not (tmp_path / "reports/runtime-inbox-acceptance/.acceptance.env").exists()
