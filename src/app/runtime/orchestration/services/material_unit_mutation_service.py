"""MaterialUnit 外层事务参与型写服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.models.material_unit import MaterialUnit, MaterialUnitStatus
from src.app.runtime.orchestration.repositories.material_unit_repository import (
    MaterialUnitRepository,
    material_unit_repository,
)
from src.utils.value_normalization import optional_int, resolve_required_pk, string_value

if TYPE_CHECKING:
    from collections.abc import Mapping


class StaleMaterialUnitPrecondition(ValueError):
    """MaterialUnit 可变事实版本与 intent 固定前置条件不一致。"""


class MaterialUnitMutationService:
    """条件创建/状态更新；事务由 Runtime write-back owner 统一提交或回滚。"""

    def __init__(self, repository: MaterialUnitRepository = material_unit_repository) -> None:
        self._repository = repository

    async def create(
        self,
        ctx: dict[str, Any],
        payload: Mapping[str, Any],
        *,
        precondition: Mapping[str, Any] | None = None,
        fact_version: str | int | None = None,
    ) -> MaterialUnit:
        from src.app.runtime.orchestration.runtime_intent_effects import (
            _apply_material_unit_status_write,
            _reject_reuse_when_owned_by_active_session,
            _state_value,
        )

        session = ctx["session"]
        db = ctx["db"]
        pkg_code = string_value(payload.get("pkg_code"), "")
        material_identity_key = string_value(payload.get("material_identity_key"), "")
        six_in_one = dict(cast("Mapping[str, Any]", payload.get("six_in_one") or {}))
        status = MaterialUnitStatus(string_value(payload.get("status"), ""))
        current_session_id = resolve_required_pk(session, "session")
        material_unit = await self._repository.get_by_pkg_code_for_update(db, pkg_code)
        if material_unit is not None and bool((precondition or {}).get("expected_absent")):
            raise StaleMaterialUnitPrecondition("material unit already exists")
        self._ensure_version(material_unit, fact_version)
        status_from_state = _state_value(getattr(material_unit, "status", None)) if material_unit is not None else None
        if material_unit is not None:
            await _reject_reuse_when_owned_by_active_session(db, material_unit, current_session_id=current_session_id)
        if material_unit is None:
            begin_nested = getattr(db, "begin_nested", None)
            if callable(begin_nested):
                try:
                    async with cast("Any", begin_nested)():
                        material_unit = MaterialUnit(
                            pkg_code=pkg_code,
                            material_identity_key=material_identity_key,
                            six_in_one=six_in_one,
                            status=status,
                            current_session_id=current_session_id,
                        )
                        await self._repository.add_and_flush(db, material_unit)
                except IntegrityError as exc:
                    material_unit = await self._repository.get_by_pkg_code_for_update(db, pkg_code)
                    if material_unit is None:
                        raise
                    if bool((precondition or {}).get("expected_absent")):
                        raise StaleMaterialUnitPrecondition("material unit was created concurrently") from exc
                    await _reject_reuse_when_owned_by_active_session(
                        db, material_unit, current_session_id=current_session_id
                    )
                    status_from_state = _state_value(getattr(material_unit, "status", None))
            else:
                material_unit = MaterialUnit(
                    pkg_code=pkg_code,
                    material_identity_key=material_identity_key,
                    six_in_one=six_in_one,
                    status=status,
                    current_session_id=current_session_id,
                )
                await self._repository.add_and_flush(db, material_unit)

        material_unit.material_identity_key = material_identity_key
        material_unit.six_in_one = {
            **dict(material_unit.six_in_one or {}),
            **{key: value for key, value in six_in_one.items() if value is not None},
        }
        if "current_location" in payload:
            material_unit.current_location = payload.get("current_location")
        _apply_material_unit_status_write(ctx, material_unit, from_state=status_from_state, to_status=status)
        material_unit.current_session_id = current_session_id
        await self._repository.flush(db)
        session.current_material_unit_id = resolve_required_pk(material_unit, "material_unit")
        return material_unit

    async def update_status(
        self,
        ctx: dict[str, Any],
        payload: Mapping[str, Any],
        *,
        fact_version: str | int | None = None,
    ) -> MaterialUnit | None:
        from src.app.runtime.orchestration.runtime_intent_effects import (
            _apply_material_unit_status_write,
            _persist_pending_cleanup_ids,
            _state_value,
        )

        material_unit_id = optional_int(payload.get("material_unit_id"))
        if material_unit_id is None:
            raise ValueError("material_unit_id must be a positive integer")
        material_unit = await self._repository.get_by_id_for_update(ctx["db"], material_unit_id)
        if material_unit is None:
            raise ValueError(f"material unit not found: {material_unit_id}")
        self._ensure_version(material_unit, fact_version)
        session = ctx["session"]
        current_session_id = resolve_required_pk(session, "session")
        if payload.get("clear_session_reference") is True and (
            getattr(material_unit, "current_session_id", None) != current_session_id
            or getattr(session, "current_material_unit_id", None) != material_unit_id
        ):
            return None
        from_status = _state_value(getattr(material_unit, "status", None))
        to_status = MaterialUnitStatus(string_value(payload.get("status"), ""))
        _apply_material_unit_status_write(ctx, material_unit, from_state=from_status, to_status=to_status)
        if "current_location" in payload:
            material_unit.current_location = payload.get("current_location")
        material_unit.current_session_id = current_session_id
        if payload.get("clear_session_reference") is True:
            cleanup_ids = ctx.setdefault("_runtime_material_unit_cleanup_ids", set())
            cleanup_ids.add(material_unit_id)
            _persist_pending_cleanup_ids(session, set(cleanup_ids))
        else:
            session.current_material_unit_id = material_unit_id
        await self._repository.flush(ctx["db"])
        return material_unit

    @staticmethod
    def _ensure_version(material_unit: MaterialUnit | None, fact_version: str | int | None) -> None:
        if fact_version is None or material_unit is None:
            return
        expected: int | None
        if isinstance(fact_version, int) and not isinstance(fact_version, bool):
            expected = fact_version
        elif isinstance(fact_version, str):
            suffix = fact_version.rsplit(":", 1)[-1]
            version_text = suffix[1:] if suffix.startswith("v") else suffix
            expected = int(version_text) if version_text.isdigit() else None
        else:
            expected = None
        actual = optional_int(getattr(material_unit, "version", None))
        if expected is not None and actual != expected:
            raise StaleMaterialUnitPrecondition("material unit fact version changed")


material_unit_mutation_service = MaterialUnitMutationService()

__all__ = [
    "MaterialUnitMutationService",
    "StaleMaterialUnitPrecondition",
    "material_unit_mutation_service",
]
