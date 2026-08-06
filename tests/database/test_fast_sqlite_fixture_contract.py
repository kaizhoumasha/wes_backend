"""FAST SQLite 共享 fixture 的性能与隔离合同。"""

import tests.conftest as shared_fixtures
from tests.core import test_schema_loader


def test_fast_database_schema_is_created_once_per_test_session() -> None:
    """完整 SQLModel schema 不得为每个 FAST 用例重复创建。"""
    marker = shared_fixtures.db_engine._fixture_function_marker

    assert marker.scope == "session"


def test_schema_loader_reuses_shared_database_fixtures() -> None:
    """Schema loader 测试不得维护第二套完整 schema fixture。"""
    assert "db_engine" not in vars(test_schema_loader)
    assert "db_session" not in vars(test_schema_loader)
