"""QA ISSUE-004：核心 pytest 拓扑配置改动应有明确的 HEAVY NONE 分类。"""

from pathlib import Path

from scripts.select_heavy_tests import load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "docs" / "architecture" / "heavy-test-impact.toml"


def test_pyproject_test_topology_change_is_explicitly_classified_as_no_heavy() -> None:
    """FAST/QUALITY 收集与报告配置由自身门禁承接，不触发运行时 HEAVY。"""

    assert select_heavy_tests(["pyproject.toml"], load_config(MAPPING_PATH)) == []
