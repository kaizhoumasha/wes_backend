"""material_units.current_location 与 resource active projection 一致性服务。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(slots=True)
class MaterialLocationConsistencyIssue:
    """单个 PKG 的 location 一致性问题。"""

    reason_code: str
    pkg_code: str
    material_unit: Any | None = None
    current_location: str | None = None
    projection_location: str | None = None
    active_mount_ids: list[int | None] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MaterialLocationRepairResult:
    """current_location 修复结果。"""

    dry_run: bool
    updated: list[dict[str, Any]] = field(default_factory=list)
    reconciling: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)


class MaterialLocationRepositoryPersistence:
    """material_units location repair 的默认持久化适配器。"""

    def __init__(self, *, db: Any | None = None) -> None:
        self.db = db

    def bind(self, db: Any) -> MaterialLocationRepositoryPersistence:
        return MaterialLocationRepositoryPersistence(db=db)

    def update_current_location(self, material_unit: Any, current_location: str) -> None:
        material_unit.current_location = current_location
        self._add(material_unit)

    def mark_reconciling(self, material_unit: Any | None, _reason_code: str) -> None:
        if material_unit is None:
            return
        material_unit.status = "RECONCILING"
        self._add(material_unit)

    def _add(self, material_unit: Any) -> None:
        if self.db is not None and hasattr(self.db, "add"):
            # 只挂入当前事务；flush/commit 由调用方的 service/db session 生命周期统一控制。
            self.db.add(material_unit)


class MaterialLocationConsistencyService:
    """按 resource projection 为权威校验/修复 material_units.current_location。"""

    def __init__(self, *, persistence: Any | None = None, hold_creator: Any | None = None) -> None:
        self.persistence = persistence if persistence is not None else MaterialLocationRepositoryPersistence()
        self.hold_creator = hold_creator

    def with_db(self, db: Any) -> MaterialLocationConsistencyService:
        bind = getattr(self.persistence, "bind", None)
        persistence = bind(db) if callable(bind) else self.persistence
        return MaterialLocationConsistencyService(persistence=persistence, hold_creator=self.hold_creator)

    def diagnose(
        self,
        *,
        material_units: Iterable[Any],
        active_mounts: Iterable[Any],
        active_occupancies: Iterable[Any],
    ) -> list[MaterialLocationConsistencyIssue]:
        occupancy_by_id = {
            occupancy_id: occupancy
            for occupancy in active_occupancies
            if (occupancy_id := getattr(occupancy, "id", None)) is not None
        }
        mounts_by_pkg: dict[str, list[Any]] = defaultdict(list)
        for mount in active_mounts:
            if getattr(mount, "ended_at", None) is not None:
                continue
            pkg_code = getattr(mount, "pkg_code", None)
            if pkg_code:
                mounts_by_pkg[str(pkg_code)].append(mount)

        issues: list[MaterialLocationConsistencyIssue] = []
        for material_unit in material_units:
            pkg_code = getattr(material_unit, "pkg_code", None)
            if not pkg_code:
                continue
            pkg_mounts = mounts_by_pkg.get(str(pkg_code), [])
            current_location = getattr(material_unit, "current_location", None)
            if not pkg_mounts:
                if current_location:
                    issues.append(
                        MaterialLocationConsistencyIssue(
                            reason_code="MISSING_ACTIVE_MOUNT",
                            pkg_code=str(pkg_code),
                            material_unit=material_unit,
                            current_location=current_location,
                        )
                    )
                continue
            if len(pkg_mounts) > 1:
                issues.append(
                    MaterialLocationConsistencyIssue(
                        reason_code="MULTIPLE_ACTIVE_MOUNTS",
                        pkg_code=str(pkg_code),
                        material_unit=material_unit,
                        current_location=current_location,
                        active_mount_ids=[getattr(mount, "id", None) for mount in pkg_mounts],
                    )
                )
                continue

            mount = pkg_mounts[0]
            occupancy_id = getattr(mount, "bin_cell_occupancy_id", None)
            occupancy = occupancy_by_id.get(occupancy_id)
            if occupancy_id is not None and occupancy is None:
                issues.append(
                    MaterialLocationConsistencyIssue(
                        reason_code="MISSING_ACTIVE_OCCUPANCY",
                        pkg_code=str(pkg_code),
                        material_unit=material_unit,
                        current_location=current_location,
                        active_mount_ids=[getattr(mount, "id", None)],
                        details={"bin_cell_occupancy_id": occupancy_id},
                    )
                )
                continue

            projection_location = self._location_from_projection(mount=mount, occupancy=occupancy)
            if not projection_location:
                issues.append(
                    MaterialLocationConsistencyIssue(
                        reason_code="MISSING_PROJECTION_LOCATION",
                        pkg_code=str(pkg_code),
                        material_unit=material_unit,
                        current_location=current_location,
                        active_mount_ids=[getattr(mount, "id", None)],
                    )
                )
                continue
            if current_location != projection_location:
                issues.append(
                    MaterialLocationConsistencyIssue(
                        reason_code="LOCATION_MISMATCH",
                        pkg_code=str(pkg_code),
                        material_unit=material_unit,
                        current_location=current_location,
                        projection_location=projection_location,
                        active_mount_ids=[getattr(mount, "id", None)],
                    )
                )
        return issues

    def repair(
        self,
        issues: Iterable[MaterialLocationConsistencyIssue],
        *,
        confirm: bool = False,
    ) -> MaterialLocationRepairResult:
        result = MaterialLocationRepairResult(dry_run=not confirm)
        for issue in issues:
            if issue.reason_code != "LOCATION_MISMATCH":
                if confirm:
                    self._mark_reconciling(issue)
                result.reconciling.append({"pkg_code": issue.pkg_code, "reason_code": issue.reason_code})
                continue
            if issue.material_unit is None or not issue.projection_location:
                result.skipped.append({"pkg_code": issue.pkg_code, "reason_code": "MISSING_REPAIR_TARGET"})
                continue
            change = {"pkg_code": issue.pkg_code, "from": issue.current_location, "to": issue.projection_location}
            if not confirm:
                result.skipped.append({**change, "reason_code": "DRY_RUN"})
                continue
            self._update_current_location(issue.material_unit, issue.projection_location)
            result.updated.append(change)
        return result

    def _update_current_location(self, material_unit: Any, current_location: str) -> None:
        if self.persistence is not None and hasattr(self.persistence, "update_current_location"):
            self.persistence.update_current_location(material_unit, current_location)
            return
        material_unit.current_location = current_location

    def _mark_reconciling(self, issue: MaterialLocationConsistencyIssue) -> None:
        if self.persistence is not None and hasattr(self.persistence, "mark_reconciling"):
            self.persistence.mark_reconciling(issue.material_unit, issue.reason_code)
        elif issue.material_unit is not None:
            issue.material_unit.status = "RECONCILING"
        if self.hold_creator is not None and hasattr(self.hold_creator, "create_hold"):
            self.hold_creator.create_hold(issue)

    @staticmethod
    def _location_from_projection(*, mount: Any, occupancy: Any | None) -> str | None:
        bin_code = getattr(occupancy, "bin_code", None) if occupancy is not None else getattr(mount, "bin_code", None)
        bin_cell_index = (
            getattr(occupancy, "bin_cell_index", None)
            if occupancy is not None
            else getattr(mount, "bin_cell_index", None)
        )
        if not bin_code or bin_cell_index is None:
            return None
        return f"{bin_code}:{bin_cell_index}"


material_location_consistency_service = MaterialLocationConsistencyService()

__all__ = [
    "MaterialLocationConsistencyIssue",
    "MaterialLocationConsistencyService",
    "MaterialLocationRepairResult",
    "MaterialLocationRepositoryPersistence",
    "material_location_consistency_service",
]
