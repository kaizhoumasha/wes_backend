"""Final branch review 指定测试文件的职责/体量护栏。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FOCUSED_TEST_FILES = (
    "tests/integration/test_workline_migration_inventory_postgresql.py",
    "tests/integration/test_runtime_plugin_binding_ddl_postgresql.py",
    "tests/workline_runtime/extensions/test_plugin_binding_runtime_wiring.py",
    "tests/workline_runtime/extensions/test_runtime_plugin_binding_required.py",
    "tests/workline_runtime/extensions/test_runtime_extension_index_generation.py",
    "tests/workline_runtime/extensions/test_runtime_extension_registration_identity.py",
)


def test_final_branch_review_test_files_each_stay_below_one_thousand_lines() -> None:
    missing = [path for path in FOCUSED_TEST_FILES if not (REPO_ROOT / path).is_file()]
    assert missing == []
    oversized = {
        path: len((REPO_ROOT / path).read_text(encoding="utf-8").splitlines())
        for path in FOCUSED_TEST_FILES
        if len((REPO_ROOT / path).read_text(encoding="utf-8").splitlines()) >= 1000
    }
    assert oversized == {}
