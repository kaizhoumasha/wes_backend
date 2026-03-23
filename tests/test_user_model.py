from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.app.admin.models.user import User, UserCreate, UserResponse, UserUpdate
from src.utils.snowflake import generate_snowflake_id


def test_user_create_normalizes_identity_fields() -> None:
    user = UserCreate(
        username="  admin  ",  # type: ignore[attr-defined]
        email="  Admin@Example.COM  ",  # type: ignore[attr-defined]
        full_name="  Test User  ",  # type: ignore[attr-defined]
        password="secret123",
    )

    assert user.username == "admin"  # type: ignore[attr-defined]
    assert user.email == "admin@example.com"  # type: ignore[attr-defined]
    assert user.full_name == "Test User"  # type: ignore[attr-defined]


def test_user_update_normalizes_optional_fields() -> None:
    user = UserUpdate(
        email="  USER@Example.COM  ",  # type: ignore[attr-defined]
        full_name="   ",  # type: ignore[attr-defined]
        version=3,  # type: ignore[attr-defined]
    )

    assert user.email == "user@example.com"  # type: ignore[attr-defined]
    assert user.full_name is None  # type: ignore[attr-defined]


def test_user_create_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        UserCreate(
            username="admin",  # type: ignore[attr-defined]
            email="not-an-email",  # type: ignore[attr-defined]
            password="secret123",
        )


def test_user_request_schema_exposes_email_constraints_for_zod() -> None:
    email_schema = UserCreate.model_json_schema()["properties"]["email"]

    assert email_schema["type"] == "string"
    assert email_schema["format"] == "email"
    assert email_schema["maxLength"] == 100
    # EmailStr 使用 format: email 而不是 pattern（由 email-validator 处理）


def test_user_response_accepts_roles_without_permissions() -> None:
    user = SimpleNamespace(
        id=1,
        username="admin",
        email="admin@example.com",
        full_name="Admin",
        version=0,
        is_superuser=True,
        is_multi_login=False,
        created_at="2026-03-23T08:00:00Z",
        created_by=1,
        updated_at=None,
        updated_by=None,
        deleted_by=None,
        deleted_at=None,
        roles=[
            SimpleNamespace(id=1, name="管理员", description=None),
            SimpleNamespace(id=2, name="审计员", description="只读"),
        ],
    )

    response = UserResponse.model_validate(user)

    assert [role.id for role in response.roles] == [1, 2]
    assert [role.name for role in response.roles] == ["管理员", "审计员"]


@pytest.mark.asyncio
async def test_user_unique_indexes_block_active_duplicates(db_session) -> None:
    db_session.add(
        User(
            id=generate_snowflake_id(),
            username="admin",
            email="admin@example.com",
            hashed_password="hashed",
        )
    )
    await db_session.commit()

    db_session.add(
        User(
            id=generate_snowflake_id(),
            username="admin",
            email="other@example.com",
            hashed_password="hashed",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_user_unique_indexes_allow_reuse_after_soft_delete(db_session) -> None:
    db_session.add(
        User(
            id=generate_snowflake_id(),
            username="admin",
            email="admin@example.com",
            hashed_password="hashed",
            is_deleted=True,
        )
    )
    await db_session.commit()

    db_session.add(
        User(
            id=generate_snowflake_id(),
            username="admin",
            email="admin@example.com",
            hashed_password="hashed",
        )
    )
    await db_session.commit()
