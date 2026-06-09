from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_integration_conftest():
    conftest_path = Path(__file__).parents[1] / "integration" / "conftest.py"
    spec = importlib.util.spec_from_file_location("workline_integration_conftest_under_test", conftest_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_integration_database_url_safety_guard_allows_docker_local_and_test_urls(monkeypatch) -> None:
    integration_conftest = _load_integration_conftest()
    monkeypatch.delenv("ALLOW_SHARED_DEV_DB_INTEGRATION", raising=False)

    assert integration_conftest._is_safe_integration_database_url("postgresql+asyncpg://u:p@db:5432/wes_db")
    assert integration_conftest._is_safe_integration_database_url("postgresql+asyncpg://u:p@localhost:5432/wes_db")
    assert integration_conftest._is_safe_integration_database_url("postgresql+asyncpg://u:p@db.example/wes_db_test")
    assert not integration_conftest._is_safe_integration_database_url("postgresql+asyncpg://u:p@prod.db/wes_db")

    monkeypatch.setenv("ALLOW_SHARED_DEV_DB_INTEGRATION", "1")
    assert integration_conftest._is_safe_integration_database_url("postgresql+asyncpg://u:p@prod.db/wes_db")
