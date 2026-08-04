from pathlib import Path

import pytest

from scripts.select_heavy_tests import SelectorError, load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]


# Regression: ISSUE-009 — 测试收敛分支误改核心环境配置并绕过 HEAVY fail-closed
# Found by /qa on 2026-08-02
def test_core_env_profiles_remain_fail_closed_without_authoritative_heavy_mapping() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    for env_file in (".env.dev", ".env.test", ".env.prod"):
        with pytest.raises(SelectorError, match="未配置 mapping/NONE"):
            select_heavy_tests([env_file], config)
