from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.app.admin.models.user import User, UserCreate, UserUpdate
from src.utils.snowflake import generate_snowflake_id


def test_user_create_normalizes_identity_fields() -> None:
    user = UserCreate(
        username="  admin  ",
        email="  Admin@Example.COM  ",
        full_name="  Test User  ",
        password="secret123",
    )

    assert user.username == "admin"
    assert user.email == "admin@example.com"
    assert user.full_name == "Test User"


def test_user_update_normalizes_optional_fields() -> None:
    user = UserUpdate(
        email="  USER@Example.COM  ",
        full_name="   ",
        version=3,
    )

    assert user.email == "user@example.com"
    assert user.full_name is None


def test_user_create_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        UserCreate(
            username="admin",
            email="not-an-email",
            password="secret123",
        )


def test_user_request_schema_exposes_email_constraints_for_zod() -> None:
    email_schema = UserCreate.model_json_schema()["properties"]["email"]

    assert email_schema["type"] == "string"
    assert email_schema["format"] == "email"
    assert email_schema["maxLength"] == 100
    # EmailStr 使用 format: email 而不是 pattern（由 email-validator 处理）


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
