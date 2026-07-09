"""Resource active projection integrity diagnostics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(slots=True, frozen=True)
class ResourceProjectionIntegrityIssue:
    """单条 resource 投影脏数据明细。"""

    reason_code: str
    projection_type: str
    object_key: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResourceProjectionIntegrityReport:
    """Resource 投影完整性诊断报告。"""

    issues: list[ResourceProjectionIntegrityIssue] = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def by_reason(self, reason_code: str) -> list[ResourceProjectionIntegrityIssue]:
        return [issue for issue in self.issues if issue.reason_code == reason_code]


class ResourceProjectionIntegrityService:
    """诊断 resource facts/active projection 的应用层完整性，不自动修复。"""

    def diagnose(
        self,
        *,
        rack_placements: Iterable[Any] = (),
        rack_bin_mounts: Iterable[Any] = (),
        bin_placements: Iterable[Any] = (),
        mounts: Iterable[Any] = (),
        occupancies: Iterable[Any] = (),
        sessions: Iterable[int] = (),
        active_session_rows: Iterable[tuple[str, Any]] = (),
        material_units: Iterable[Any] = (),
    ) -> ResourceProjectionIntegrityReport:
        rack_placements = list(rack_placements)
        rack_bin_mounts = list(rack_bin_mounts)
        bin_placements = list(bin_placements)
        mounts = list(mounts)
        occupancies = list(occupancies)
        sessions = list(sessions)
        active_session_rows = list(active_session_rows)
        material_units = list(material_units)

        report = ResourceProjectionIntegrityReport()
        occupancy_by_id = {
            occupancy_id: occupancy
            for occupancy in occupancies
            if (occupancy_id := getattr(occupancy, "id", None)) is not None
        }
        session_ids = {int(session_id) for session_id in sessions}

        self._append_orphan_mount_issues(report, mounts=mounts, occupancy_by_id=occupancy_by_id)
        self._append_orphan_session_issues(report, rows=active_session_rows, session_ids=session_ids)
        self._append_active_duplicate_issues(
            report,
            rack_placements=rack_placements,
            rack_bin_mounts=rack_bin_mounts,
            bin_placements=bin_placements,
            mounts=mounts,
            occupancies=occupancies,
        )
        self._append_material_location_drift_issues(report, mounts=mounts, material_units=material_units)
        return report

    def _append_orphan_mount_issues(
        self,
        report: ResourceProjectionIntegrityReport,
        *,
        mounts: Iterable[Any],
        occupancy_by_id: dict[int, Any],
    ) -> None:
        for mount in mounts:
            occupancy_id = getattr(mount, "bin_cell_occupancy_id", None)
            if occupancy_id is None or occupancy_id in occupancy_by_id:
                continue
            report.issues.append(
                ResourceProjectionIntegrityIssue(
                    reason_code="ORPHAN_MOUNT_OCCUPANCY",
                    projection_type="BIN_MATERIAL_MOUNT",
                    object_key=str(getattr(mount, "pkg_code", None) or getattr(mount, "id", "")),
                    details={
                        "mount_id": getattr(mount, "id", None),
                        "pkg_code": getattr(mount, "pkg_code", None),
                        "bin_cell_occupancy_id": occupancy_id,
                    },
                )
            )

    def _append_orphan_session_issues(
        self,
        report: ResourceProjectionIntegrityReport,
        *,
        rows: Iterable[tuple[str, Any]],
        session_ids: set[int],
    ) -> None:
        for table_name, row in rows:
            workline_session_id = getattr(row, "workline_session_id", None)
            if workline_session_id is None or int(workline_session_id) in session_ids:
                continue
            report.issues.append(
                ResourceProjectionIntegrityIssue(
                    reason_code="ORPHAN_WORKLINE_SESSION",
                    projection_type=table_name,
                    object_key=str(getattr(row, "pkg_code", None) or getattr(row, "id", "")),
                    details={
                        "table_name": table_name,
                        "row_id": getattr(row, "id", None),
                        "workline_session_id": workline_session_id,
                    },
                )
            )

    def _append_active_duplicate_issues(
        self,
        report: ResourceProjectionIntegrityReport,
        *,
        rack_placements: Iterable[Any],
        rack_bin_mounts: Iterable[Any],
        bin_placements: Iterable[Any],
        mounts: Iterable[Any],
        occupancies: Iterable[Any],
    ) -> None:
        self._append_duplicates_by_key(
            report,
            projection_type="RACK_PLACEMENT",
            rows=rack_placements,
            key_parts=("rack_code",),
        )
        self._append_duplicates_by_key(
            report,
            projection_type="RACK_BIN_MOUNT_SLOT",
            rows=rack_bin_mounts,
            key_parts=("rack_code", "rack_slot_code"),
        )
        self._append_duplicates_by_key(
            report,
            projection_type="RACK_BIN_MOUNT_BIN",
            rows=rack_bin_mounts,
            key_parts=("bin_code",),
        )
        self._append_duplicates_by_key(
            report,
            projection_type="BIN_PLACEMENT_BIN",
            rows=bin_placements,
            key_parts=("bin_code",),
        )
        self._append_duplicates_by_key(
            report,
            projection_type="BIN_PLACEMENT_PLACEHOLDER",
            rows=bin_placements,
            key_parts=("placeholder_key",),
        )
        self._append_duplicates_by_key(
            report,
            projection_type="BIN_MATERIAL_MOUNT_PKG",
            rows=mounts,
            key_parts=("pkg_code",),
        )
        self._append_duplicates_by_key(
            report,
            projection_type="BIN_CELL_OCCUPANCY",
            rows=occupancies,
            key_parts=("bin_code", "bin_cell_index"),
        )

    def _append_duplicates_by_key(
        self,
        report: ResourceProjectionIntegrityReport,
        *,
        projection_type: str,
        rows: Iterable[Any],
        key_parts: tuple[str, ...],
    ) -> None:
        grouped: dict[tuple[str, ...], list[Any]] = defaultdict(list)
        for row in rows:
            if getattr(row, "ended_at", None) is not None:
                continue
            values = tuple(str(value) for key in key_parts if (value := getattr(row, key, None)) is not None)
            if len(values) != len(key_parts):
                continue
            grouped[values].append(row)

        for key, duplicates in grouped.items():
            if len(duplicates) <= 1:
                continue
            object_key = ":".join(key)
            report.issues.append(
                ResourceProjectionIntegrityIssue(
                    reason_code="ACTIVE_DUPLICATE",
                    projection_type=projection_type,
                    object_key=object_key,
                    details={
                        "key": object_key,
                        "key_parts": key_parts,
                        "active_ids": [getattr(row, "id", None) for row in duplicates],
                    },
                )
            )

    def _append_material_location_drift_issues(
        self,
        report: ResourceProjectionIntegrityReport,
        *,
        mounts: Iterable[Any],
        material_units: Iterable[Any],
    ) -> None:
        active_location_by_pkg = {
            pkg_code: f"{getattr(mount, 'bin_code', '')}:{getattr(mount, 'bin_cell_index', '')}"
            for mount in mounts
            if getattr(mount, "ended_at", None) is None and (pkg_code := getattr(mount, "pkg_code", None))
        }
        for material_unit in material_units:
            pkg_code = getattr(material_unit, "pkg_code", None)
            if not pkg_code or pkg_code not in active_location_by_pkg:
                continue
            expected_location = active_location_by_pkg[pkg_code]
            current_location = getattr(material_unit, "current_location", None)
            if current_location == expected_location:
                continue
            report.issues.append(
                ResourceProjectionIntegrityIssue(
                    reason_code="MATERIAL_LOCATION_DRIFT",
                    projection_type="MATERIAL_UNIT",
                    object_key=str(pkg_code),
                    details={
                        "pkg_code": pkg_code,
                        "current_location": current_location,
                        "projection_location": expected_location,
                    },
                )
            )


resource_projection_integrity_service = ResourceProjectionIntegrityService()

__all__ = [
    "ResourceProjectionIntegrityIssue",
    "ResourceProjectionIntegrityReport",
    "ResourceProjectionIntegrityService",
    "resource_projection_integrity_service",
]
