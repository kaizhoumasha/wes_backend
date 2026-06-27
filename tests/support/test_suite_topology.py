"""测试套件目录拓扑扫描工具。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
__test__ = False

MAX_TEST_FILE_LINES = 3000
DEFAULT_EXCLUDED_TEST_DIRS = {"tests/e2e", "tests/integration", "tests/resilience", "tests/load", "tests/mock"}

# tests/ 根目录不再允许放置测试文件。
ROOT_LEVEL_TEST_FILE_ALLOWLIST = set[str]()

# 第一阶段仅允许已确认超大文件继续存在；后续拆分任务会逐项移除。
TEST_FILE_LINE_LIMIT_ALLOWLIST = set[str]()


def iter_test_files() -> list[Path]:
    """返回仓库内所有 pytest 测试文件。"""

    return sorted(TESTS_ROOT.rglob("test_*.py"))


def line_count(path: Path) -> int:
    """统计单个文件行数。"""

    return len(path.read_text(encoding="utf-8").splitlines())


def root_level_test_files() -> list[Path]:
    """返回 tests/ 根目录下的测试文件。"""

    return sorted(TESTS_ROOT.glob("test_*.py"))


def test_files_over_line_limit() -> list[Path]:
    """返回超过单文件行数上限的测试文件。"""

    return [path for path in iter_test_files() if line_count(path) > MAX_TEST_FILE_LINES]
