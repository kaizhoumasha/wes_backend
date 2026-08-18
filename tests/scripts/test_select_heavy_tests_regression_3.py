"""核心 workspace 配置变化必须选择 concrete rough sorter PostgreSQL owner。"""

from pathlib import Path

from scripts.select_heavy_tests import load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "docs" / "architecture" / "heavy-test-impact.toml"


def test_pyproject_workspace_change_selects_concrete_rough_sorter_owner() -> None:
    """workspace/plugin 依赖拓扑不能只靠 FAST/QUALITY 收集证明。"""

    assert select_heavy_tests(["pyproject.toml"], load_config(MAPPING_PATH)) == [
        "tests/integration/execution/test_decision_processing_postgresql.py"
    ]
