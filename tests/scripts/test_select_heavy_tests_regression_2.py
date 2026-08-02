"""QA ISSUE-002：已退役 TEST 部署插件数据步骤不得阻塞核心 HEAVY 选择。"""

from pathlib import Path

from scripts.select_heavy_tests import load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "docs" / "architecture" / "heavy-test-impact.toml"


def test_test_deploy_pipeline_is_explicitly_ignored_by_core_heavy_selector() -> None:
    """具体插件数据同步移出部署入口后，不应触发核心业务 HEAVY 测试。"""

    assert select_heavy_tests(["Jenkinsfile.test-deploy"], load_config(MAPPING_PATH)) == []
