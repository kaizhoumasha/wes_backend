"""数据库运行角色与单进程连接预算配置合同。"""

from __future__ import annotations

import secrets

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from src.core.conf import Settings


def _valid_settings(**overrides: object) -> dict[str, object]:
    return {
        "_env_file": None,
        "JWT_SECRET_KEY": secrets.token_urlsafe(32),
        "API_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "POSTGRES_PASSWORD": "strong-database-secret",
        "DATABASE_RUNTIME_ROLE": "api",
        "DATABASE_POOL_SIZE": 5,
        "DATABASE_MAX_OVERFLOW": 0,
        "DATABASE_APPLICATION_RUN_ID": "unit-run-unique",
        **overrides,
    }


@pytest.mark.parametrize("role", ["api", "celery", "cli", "integration"])
def test_database_runtime_roles_accept_only_budgeted_pool_sizes(role: str) -> None:
    pool_size = 5 if role == "api" else 1

    configured = Settings(**_valid_settings(DATABASE_RUNTIME_ROLE=role, DATABASE_POOL_SIZE=pool_size))

    assert role == configured.DATABASE_RUNTIME_ROLE
    assert pool_size == configured.DATABASE_POOL_SIZE
    assert configured.DATABASE_MAX_OVERFLOW == 0


@pytest.mark.parametrize(
    ("role", "pool_size", "max_overflow"),
    [
        ("api", 6, 0),
        ("api", 5, 1),
        ("celery", 2, 0),
        ("cli", 2, 0),
        ("integration", 2, 0),
    ],
)
def test_database_runtime_role_rejects_illegal_pool_budget(
    role: str,
    pool_size: int,
    max_overflow: int,
) -> None:
    with pytest.raises(ValidationError, match="DATABASE_"):
        Settings(
            **_valid_settings(
                DATABASE_RUNTIME_ROLE=role,
                DATABASE_POOL_SIZE=pool_size,
                DATABASE_MAX_OVERFLOW=max_overflow,
            )
        )


def test_database_runtime_role_is_required() -> None:
    values = _valid_settings()
    values.pop("DATABASE_RUNTIME_ROLE")

    with pytest.raises(ValidationError, match="DATABASE_RUNTIME_ROLE"):
        Settings(**values)


def test_database_runtime_role_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="DATABASE_RUNTIME_ROLE"):
        Settings(**_valid_settings(DATABASE_RUNTIME_ROLE="beat"))


@pytest.mark.parametrize("run_id", [None, "", "   "])
def test_integration_role_requires_explicit_nonempty_run_id(run_id: str | None) -> None:
    with pytest.raises(ValidationError, match="DATABASE_APPLICATION_RUN_ID"):
        Settings(
            **_valid_settings(
                DATABASE_RUNTIME_ROLE="integration",
                DATABASE_POOL_SIZE=1,
                DATABASE_APPLICATION_RUN_ID=run_id,
            )
        )
