"""QA ISSUE-006：测试收敛分支的治理与 mock 资产必须完整映射。"""

from pathlib import Path

import pytest

from scripts.select_heavy_tests import SelectorError, load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "docs" / "architecture" / "heavy-test-impact.toml"

RETIRED_GOVERNANCE_SCRIPTS = (
    "scripts/check_runtime_evidence_readiness_gate.py",
    "scripts/compose_runtime_evidence_artifact.py",
    "scripts/data/seed_runtime_monitor_smoke.py",
    "scripts/data/sync_test_workline_devices.py",
    "scripts/generate_northbound_legacy_removal_report.py",
)


@pytest.mark.parametrize("changed_path", RETIRED_GOVERNANCE_SCRIPTS)
def test_retired_governance_script_paths_fail_closed(changed_path: str) -> None:
    assert not (REPO_ROOT / changed_path).exists()

    with pytest.raises(SelectorError, match="未配置 mapping/NONE"):
        select_heavy_tests([changed_path], load_config(MAPPING_PATH))


def test_convergence_governance_and_mock_assets_have_complete_heavy_ownership() -> None:
    """治理资产为 NONE；共享镜像运行 ECS 与 WMS Transport HEAVY owner。"""

    changed_paths = [
        "scripts/architecture-guardrails.sh",
        "scripts/check_business_legacy_absence_gate.py",
        "scripts/check_fast_test_budget.py",
        "scripts/generate_legacy_matrix.py",
        "scripts/run_selected_heavy_tests.py",
        "scripts/test_live_suite.sh",
        "scripts/workline_inbox_retirement_guardrail.py",
        "tests/mock/Dockerfile",
        "tests/mock/ecs_mock_catalog.py",
        "tests/mock/ecs_mock_server.py",
        "tests/support/test_suite_topology.py",
    ]

    assert select_heavy_tests(changed_paths, load_config(MAPPING_PATH)) == [
        "tests/mock/test_ecs_mock_server.py",
        "tests/mock/test_mock_dockerfile.py",
        "tests/mock/test_wms_transport_mock_server.py",
    ]
