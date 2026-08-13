import hashlib
from pathlib import Path

from scripts.select_heavy_tests import load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]


# Regression: ISSUE-009 — 测试收敛分支误改核心环境配置并绕过 HEAVY fail-closed
# Found by /qa on 2026-08-02
def test_core_env_profiles_use_fingerprinted_authoritative_heavy_mapping() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    expected = [
        "tests/e2e/device_command/test_device_command_production_wiring.py",
        "tests/integration/test_wms_northbound_feasibility_probe.py",
        "tests/mock/test_wms_mock_server.py",
        "tests/mock/test_wms_northbound_contract.py",
    ]

    for env_file in (".env.dev", ".env.test", ".env.prod"):
        mapping = next(mapping for mapping in config[1] if mapping.source_glob == env_file)
        assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / env_file).read_bytes()).hexdigest()
        assert select_heavy_tests([env_file], config, repo_root=REPO_ROOT) == expected
