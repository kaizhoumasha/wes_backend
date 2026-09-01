from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.admin.models.role import RoleCreate


def test_role_create_rejects_empty_name_and_exposes_contract_constraint() -> None:
    with pytest.raises(ValidationError):
        RoleCreate(name="")  # type: ignore[attr-defined]

    name_schema = RoleCreate.model_json_schema()["properties"]["name"]
    assert name_schema["minLength"] == 1
