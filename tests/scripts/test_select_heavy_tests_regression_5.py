"""QA ISSUE-006：测试收敛分支的治理与 mock 资产必须完整映射。"""

from pathlib import Path

from scripts.select_heavy_tests import load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "docs" / "architecture" / "heavy-test-impact.toml"


def test_convergence_governance_and_mock_assets_have_complete_heavy_ownership() -> None:
    """治理/退役插件资产为 NONE；保留 ECS mock 运行其现存核心 HEAVY 消费者。"""

    changed_paths = [
        "scripts/check_business_legacy_absence_gate.py",
        "scripts/check_fast_test_budget.py",
        "scripts/check_runtime_evidence_readiness_gate.py",
        "scripts/compose_runtime_evidence_artifact.py",
        "scripts/data/seed_runtime_monitor_smoke.py",
        "scripts/data/sync_test_workline_devices.py",
        "scripts/generate_legacy_matrix.py",
        "scripts/generate_northbound_legacy_removal_report.py",
        "scripts/test_live_suite.sh",
        "scripts/workline_inbox_retirement_guardrail.py",
        "tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json",
        "tests/fixtures/workline_contract/start_admission/README.md",
        "tests/mock/Dockerfile",
        "tests/mock/device_simulator.py",
        "tests/mock/ecs_mock_catalog.py",
        "tests/mock/ecs_mock_server.py",
        "tests/support/smt_sorting_inbound_postgresql.py",
        "tests/support/test_suite_topology.py",
        "tests/support/wms_conveyor_batch_postgresql.py",
        "tests/support/wms_full_box_exchange_postgresql.py",
    ]

    assert select_heavy_tests(changed_paths, load_config(MAPPING_PATH)) == [
        "tests/integration/test_mock_container_entrypoints.py",
        "tests/mock/test_ecs_mock_server.py",
        "tests/mock/test_mock_dockerfile.py",
    ]
