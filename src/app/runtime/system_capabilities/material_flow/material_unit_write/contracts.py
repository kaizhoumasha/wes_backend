"""MaterialUnit EFFECT typed input/output。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator

MaterialUnitFactVersion = StrictInt | Annotated[str, StringConstraints(pattern=r"^material-unit:v\d+$")]


class MaterialUnitWritePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_absent: StrictBool | None = None


class MaterialUnitWriteAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    precondition: MaterialUnitWritePrecondition
    fact_version: MaterialUnitFactVersion


class MaterialUnitWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["CREATE", "UPDATE_STATUS", "MARK_NG"]
    pkg_code: str | None = Field(default=None, min_length=1, max_length=160)
    material_identity_key: str | None = Field(default=None, min_length=1, max_length=160)
    six_in_one: dict[str, Any] | None = None
    material_unit_id: int | None = Field(default=None, gt=0)
    status: str = Field(min_length=1, max_length=40)
    current_location: str | None = Field(default=None, max_length=160)
    clear_session_reference: bool = False

    @model_validator(mode="after")
    def validate_operation_fields(self) -> MaterialUnitWriteInput:
        if self.operation == "CREATE":
            if not self.pkg_code or not self.material_identity_key or self.six_in_one is None:
                raise ValueError("CREATE requires pkg_code, material_identity_key and six_in_one")
            if self.material_unit_id is not None or self.clear_session_reference:
                raise ValueError("CREATE must not include update-only fields")
        elif self.material_unit_id is None:
            raise ValueError(f"{self.operation} requires material_unit_id")
        elif self.pkg_code is not None or self.material_identity_key is not None or self.six_in_one is not None:
            raise ValueError(f"{self.operation} must not include create-only fields")
        if self.operation == "MARK_NG" and self.status != "NG":
            raise ValueError("MARK_NG requires status=NG")
        return self


class MaterialUnitWriteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    material_unit_id: int
    status: str


__all__ = [
    "MaterialUnitWriteAdmission",
    "MaterialUnitWriteInput",
    "MaterialUnitWriteOutput",
    "MaterialUnitWritePrecondition",
]
