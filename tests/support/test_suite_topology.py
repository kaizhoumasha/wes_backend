"""测试套件目录拓扑扫描工具。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
__test__ = False

MAX_TEST_FILE_LINES = 3000
DEFAULT_EXCLUDED_TEST_DIRS = {"tests/e2e", "tests/resilience", "tests/load", "tests/mock"}

# 历史遗留的根目录测试文件。新增领域测试不应继续放在 tests/ 根目录。
ROOT_LEVEL_TEST_FILE_ALLOWLIST = {
    "tests/test_api_app_service_cache.py",
    "tests/test_api_application_routes.py",
    "tests/test_audit_log_service.py",
    "tests/test_base_api.py",
    "tests/test_base_repository_crud.py",
    "tests/test_base_repository_error_handling.py",
    "tests/test_base_repository_hooks.py",
    "tests/test_base_service_cache.py",
    "tests/test_celery_dev_autoreload_config.py",
    "tests/test_create_schema_compatibility.py",
    "tests/test_docker_compose_mock_urls.py",
    "tests/test_error_handlers_dbapi.py",
    "tests/test_event_stream_service.py",
    "tests/test_exceptions.py",
    "tests/test_health_endpoint.py",
    "tests/test_log_center_models.py",
    "tests/test_nginx_forwarded_headers.py",
    "tests/test_nginx_proxy_config.py",
    "tests/test_optimistic_lock.py",
    "tests/test_permission_scanner.py",
    "tests/test_permission_service_app_cache.py",
    "tests/test_query_builder.py",
    "tests/test_rbac_cache_invalidation.py",
    "tests/test_redis_client.py",
    "tests/test_relation_metadata.py",
    "tests/test_request_parse.py",
    "tests/test_schema_loader.py",
    "tests/test_security_session_helpers.py",
    "tests/test_soft_delete_feature.py",
    "tests/test_timezone.py",
    "tests/test_tree_repository.py",
    "tests/test_tree_repository_batch_sort.py",
    "tests/test_tree_service.py",
}

# 第一阶段只允许两个已确认超大文件继续存在；后续拆分任务会逐项移除。
TEST_FILE_LINE_LIMIT_ALLOWLIST = {
    "tests/workline_runtime/test_runtime_intent_effects.py",
    "tests/api/test_workline_runtime_api.py",
}


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
