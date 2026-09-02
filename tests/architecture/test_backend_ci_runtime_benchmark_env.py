"""Backend CI runtime benchmark stage must load test environment secrets."""

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


def test_quality_gate_stage_loads_test_env_file_for_runtime_benchmark():
    """benchmark artifact 由 Quality Gate 阶段生成，CI 容器必须加载 .env.test。

    历史上曾设独立 ``Runtime Benchmark Artifact`` 阶段；按 KISS 已收敛进
    ``Verification.Quality Gate`` 阶段，质量门禁脚本与基准物合同同时复用
    同一 ``.env.test``，不再单独拉一个阶段。
    """
    stage = _stage_body("Quality Gate")

    assert '--env-file "$WORKSPACE/.env.test"' in stage
