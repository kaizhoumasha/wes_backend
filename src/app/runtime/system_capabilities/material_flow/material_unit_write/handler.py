"""MaterialUnit EFFECT handler。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success

from .contracts import MaterialUnitWriteAdmission, MaterialUnitWriteInput, MaterialUnitWriteOutput


class MaterialUnitWriteHandler:
    async def __call__(self, request: MaterialUnitWriteInput, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.material_unit_mutation_service import (
            StaleMaterialUnitPrecondition,
            material_unit_mutation_service,
        )

        ctx = execution.ctx  # type: ignore[attr-defined]
        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, MaterialUnitWriteAdmission):
            raise TypeError("material unit effect requires typed admission")
        try:
            if request.operation == "CREATE":
                material_unit = await material_unit_mutation_service.create(
                    ctx,
                    request.model_dump(mode="python", exclude_none=True, exclude={"operation"}),
                    precondition=admission.precondition.model_dump(mode="python", exclude_none=True),
                    fact_version=admission.fact_version,
                )
            else:
                material_unit = await material_unit_mutation_service.update_status(
                    ctx,
                    request.model_dump(mode="python", exclude_none=True, exclude={"operation"}),
                    fact_version=admission.fact_version,
                )
        except StaleMaterialUnitPrecondition:
            return BusinessReject(reason_code="STALE_PRECONDITION", message="material unit fact changed")
        if material_unit is None:
            return Success(
                payload=MaterialUnitWriteOutput(material_unit_id=request.material_unit_id or 0, status=request.status)
            )
        return Success(
            payload=MaterialUnitWriteOutput(
                material_unit_id=int(material_unit.id),
                status=str(getattr(getattr(material_unit, "status", None), "value", material_unit.status)),
            )
        )


__all__ = ["MaterialUnitWriteHandler"]
