"""Backend CI Phase 3 benchmark stage must load test environment secrets."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = REPO_ROOT / "Jenkinsfile.backend-ci"


def _stage_body(stage_name: str) -> str:
    source = JENKINSFILE.read_text(encoding="utf-8")
    match = re.search(rf"stage\('{re.escape(stage_name)}'\) \{{(?P<body>.*?)\n\s{{16}}\}}", source, re.DOTALL)
    assert match is not None, f"missing Jenkins stage: {stage_name}"
    return match.group("body")


def test_phase3_benchmark_artifact_stage_loads_test_env_file():
    """benchmark artifact 生成会导入 settings，CI 容器必须加载 .env.test。"""
    stage = _stage_body("Phase 3 Benchmark Artifact")

    assert '--env-file "$WORKSPACE/.env.test"' in stage
