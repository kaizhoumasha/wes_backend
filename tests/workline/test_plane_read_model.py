"""PlaneSceneView / PlaneSnapshot schema contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError


class _AuditServiceStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_audit_log(self, _db: object, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return object()


class _AuditSessionStub:
    def __init__(self) -> None:
        self.commit_count = 0

    async def commit(self) -> None:
        self.commit_count += 1


def test_plane_scene_and_snapshot_have_independent_schema_versions() -> None:
    """scene 与 snapshot 必须独立 version, 前端不能混用。"""

    from src.app.workline.models.plane import PlaneSceneView, PlaneSnapshot

    scene = PlaneSceneView(
        schema_version="plane.scene.v1",
        workline_code="WL-1",
        nodes=[{"code": "SCAN1", "label": "扫码位 1", "kind": "station"}],
        edges=[],
    )
    snapshot = PlaneSnapshot(
        schema_version="plane.snapshot.v1",
        workline_code="WL-1",
        scene_schema_version="plane.scene.v1",
        objects=[{"object_code": "BIN-1", "object_label": "料箱 1", "state": "RUNNING"}],
        extremes=[],
    )

    assert scene.schema_version == "plane.scene.v1"
    assert snapshot.schema_version == "plane.snapshot.v1"
    assert snapshot.scene_schema_version == scene.schema_version


def test_plane_read_model_rejects_label_without_code() -> None:
    """code/label 分离: 可展示 label, 但稳定 code 必填。"""

    from src.app.workline.models.plane import PlaneSceneView

    with pytest.raises(ValidationError):
        PlaneSceneView(
            schema_version="plane.scene.v1",
            workline_code="WL-1",
            nodes=[{"label": "扫码位 1", "kind": "station"}],
            edges=[],
        )


def test_plane_read_security_policy_declares_scope_redaction_and_audit_actions() -> None:
    """plane read 安全门禁必须集中声明权限、scope、脱敏与审计口径。"""

    from src.app.workline.services.plane_service import plane_read_security_policy

    assert plane_read_security_policy.scope == "WORKLINE_LOCAL"
    assert plane_read_security_policy.scene_permission == "biz:workline:view-plane-scene"
    assert plane_read_security_policy.snapshot_permission == "biz:workline:view-plane-snapshot"
    assert {"config", "runtime_config_json", "diagnostic_profile"}.issubset(
        plane_read_security_policy.redacted_workline_fields
    )
    assert plane_read_security_policy.audit_event("scene", workline_id=7, workline_code="WL-7") == {
        "action": "WORKLINE_PLANE_SCENE_READ",
        "permission": "biz:workline:view-plane-scene",
        "scope": "WORKLINE_LOCAL",
        "workline_id": "7",
        "workline_code": "WL-7",
    }
    assert plane_read_security_policy.audit_event("snapshot", workline_id=7, workline_code="WL-7")["action"] == (
        "WORKLINE_PLANE_SNAPSHOT_READ"
    )


def test_plane_read_security_policy_enforces_workline_local_owner_scope() -> None:
    """plane read 行级过滤必须约束到当前用户可见的 WorkLine。"""

    from src.app.workline.services.plane_service import PlaneReadPrincipal, plane_read_security_policy
    from src.core.exceptions import PermissionException

    workline = SimpleNamespace(id=7, line_code="WL-7", created_by=42)

    plane_read_security_policy.ensure_can_read_workline(
        workline,
        PlaneReadPrincipal(user_id=42, is_superuser=False),
    )
    plane_read_security_policy.ensure_can_read_workline(
        workline,
        PlaneReadPrincipal(user_id=99, is_superuser=True),
    )

    with pytest.raises(PermissionException, match="无权读取该 WorkLine plane 视图"):
        plane_read_security_policy.ensure_can_read_workline(
            workline,
            PlaneReadPrincipal(user_id=99, is_superuser=False),
        )


@pytest.mark.asyncio
async def test_plane_service_rejects_non_owner_plane_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """service 读取 scene/snapshot 前必须执行行级过滤。"""

    from src.app.workline.services.plane_service import PlaneReadPrincipal, WorkLinePlaneService
    from src.core.exceptions import PermissionException

    service = WorkLinePlaneService(audit_service=_AuditServiceStub())
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(return_value=SimpleNamespace(id=7, line_code="WL-7", line_name="Line 7", config={}, created_by=42)),
    )

    with pytest.raises(PermissionException, match="无权读取该 WorkLine plane 视图"):
        await service.get_scene(
            object(),
            object(),
            7,
            principal=PlaneReadPrincipal(user_id=99, is_superuser=False),
        )


@pytest.mark.asyncio
async def test_plane_service_records_read_audit_with_security_policy_args() -> None:
    """plane read audit 必须使用安全 policy 生成可查询维度。"""

    from src.app.sys.models.audit_log import OperaStatus
    from src.app.workline.services import WorkLinePlaneService

    audit_service = _AuditServiceStub()
    service = WorkLinePlaneService(audit_service=audit_service)
    db = _AuditSessionStub()

    await service.record_read_audit(db, view="scene", workline_id=7, workline_code="WL-7")

    assert audit_service.calls == [
        {
            "method": "GET",
            "title": "WorkLine Plane Scene Read",
            "path": "/work_lines/7/plane/scene",
            "args": {
                "action": "WORKLINE_PLANE_SCENE_READ",
                "permission": "biz:workline:view-plane-scene",
                "scope": "WORKLINE_LOCAL",
                "workline_id": "7",
                "workline_code": "WL-7",
                "object_type": "WorkLine",
                "object_id": "7",
                "change_summary": "read plane scene",
            },
            "status": OperaStatus.SUCCESS,
            "code": "200",
            "msg": "OK",
        }
    ]
    assert db.commit_count == 1


# --- plane.scene.v2 ---------------------------------------------------------


def test_classify_device_bindings_marks_bound_invalid_unbound_and_orphans() -> None:
    """按 Definition 声明的设备角色逐行分类；未声明的键落入孤儿绑定。"""

    from wes_plugin_sdk import WorkLineDeviceRole

    from src.app.workline.services.plane_scene_binding import (
        ClassifiedBinding,
        OrphanBinding,
        classify_device_bindings,
    )

    roles = (
        WorkLineDeviceRole(role_key="SCAN1", display_name="扫码枪 1"),
        WorkLineDeviceRole(role_key="SCAN2", display_name="扫码枪 2"),
        WorkLineDeviceRole(role_key="SCAN3", display_name="扫码枪 3"),
    )
    config = {
        "device_bindings": {
            "SCAN1": "DEV-001",
            "SCAN2": "",
            "SCAN_LEGACY": "DEV-099",
        }
    }

    result = classify_device_bindings(config, roles)

    assert result.by_key["SCAN1"] == ClassifiedBinding(bound_code="DEV-001", state="BOUND")
    assert result.by_key["SCAN2"] == ClassifiedBinding(bound_code=None, state="INVALID")
    assert result.by_key["SCAN3"] == ClassifiedBinding(bound_code=None, state="UNBOUND")
    assert result.orphans == (OrphanBinding(key="SCAN_LEGACY", bound_code="DEV-099", reason="DEFINITION_ROLE_REMOVED"),)


def test_classify_device_bindings_marks_duplicate_bound_codes_as_invalid() -> None:
    """同一设备编码被两个角色同时占用时，两行都不可信，标记为 INVALID。"""

    from wes_plugin_sdk import WorkLineDeviceRole

    from src.app.workline.services.plane_scene_binding import classify_device_bindings

    roles = (
        WorkLineDeviceRole(role_key="SCAN1", display_name="扫码枪 1"),
        WorkLineDeviceRole(role_key="SCAN2", display_name="扫码枪 2"),
    )
    config = {"device_bindings": {"SCAN1": "DEV-001", "SCAN2": "DEV-001"}}

    result = classify_device_bindings(config, roles)

    assert result.by_key["SCAN1"].state == "INVALID"
    assert result.by_key["SCAN2"].state == "INVALID"
    assert result.orphans == ()


def test_classify_position_bindings_reuses_same_classification_rules() -> None:
    """位置槽位复用同一套只读分类，不受设备分类实现变化影响。"""

    from wes_plugin_sdk import WorkLinePositionSlot

    from src.app.workline.services.plane_scene_binding import classify_position_bindings

    slots = (
        WorkLinePositionSlot(
            slot_key="FIVE_RACK",
            display_name="五层架位",
            position_type="RACK_POSITION",
            location_type="RACK",
            allowed_rack_kind="FIVE_LAYER",
        ),
    )

    bound = classify_position_bindings({"position_bindings": {"FIVE_RACK": "POS-001"}}, slots)
    unbound = classify_position_bindings({}, slots)

    assert bound.by_key["FIVE_RACK"].state == "BOUND"
    assert bound.by_key["FIVE_RACK"].bound_code == "POS-001"
    assert unbound.by_key["FIVE_RACK"].state == "UNBOUND"


def test_classify_bindings_marks_declared_rows_invalid_when_container_is_malformed() -> None:
    """绑定容器解析失败时不得把已声明资源伪装成未绑定。"""

    from wes_plugin_sdk import WorkLineDeviceRole

    from src.app.workline.services.plane_scene_binding import classify_device_bindings

    roles = (WorkLineDeviceRole(role_key="SCAN1", display_name="扫码枪 1"),)

    result = classify_device_bindings({"device_bindings": ["DEV-001"]}, roles)
    malformed_config = classify_device_bindings(["not-a-mapping"], roles)

    assert result.by_key["SCAN1"].state == "INVALID"
    assert malformed_config.by_key["SCAN1"].state == "INVALID"


def test_compute_scene_revision_changes_with_version_plugin_and_resource_definition() -> None:
    """WorkLine 版本、插件身份或 Definition 资源变化都必须改变 scene_revision。"""

    from src.app.workline.services.plane_service import WorkLinePlaneService

    base = WorkLinePlaneService.compute_scene_revision(
        workline_id=7,
        workline_version=1,
        plugin_key="manual-picking",
        plugin_version="1.0.0",
        resource_signature="SCAN1|FIVE_RACK",
    )
    same = WorkLinePlaneService.compute_scene_revision(
        workline_id=7,
        workline_version=1,
        plugin_key="manual-picking",
        plugin_version="1.0.0",
        resource_signature="SCAN1|FIVE_RACK",
    )
    version_bumped = WorkLinePlaneService.compute_scene_revision(
        workline_id=7,
        workline_version=2,
        plugin_key="manual-picking",
        plugin_version="1.0.0",
        resource_signature="SCAN1|FIVE_RACK",
    )
    plugin_bumped = WorkLinePlaneService.compute_scene_revision(
        workline_id=7,
        workline_version=1,
        plugin_key="manual-picking",
        plugin_version="1.1.0",
        resource_signature="SCAN1|FIVE_RACK",
    )
    resources_changed = WorkLinePlaneService.compute_scene_revision(
        workline_id=7,
        workline_version=1,
        plugin_key="manual-picking",
        plugin_version="1.0.0",
        resource_signature="SCAN1|SCAN2|FIVE_RACK",
    )

    assert base == same
    assert base != version_bumped
    assert base != plugin_bumped
    assert base != resources_changed


def _manual_picking_definition() -> object:
    from wes_plugin_sdk import PluginDefinition, WorkLineDeviceRole, WorkLinePositionSlot

    return PluginDefinition(
        plugin_key="manual-picking",
        plugin_version="1.0.0",
        display_name="人工拣选",
        supported_line_types=("MANUAL",),
        device_roles=(WorkLineDeviceRole(role_key="SCAN1", display_name="扫码枪 1"),),
        position_slots=(
            WorkLinePositionSlot(
                slot_key="FIVE_RACK",
                display_name="五层架位",
                position_type="RACK_POSITION",
                location_type="RACK",
                allowed_rack_kind="FIVE_LAYER",
            ),
        ),
    )


def test_build_scene_v2_assembles_resource_groups_and_orphan_diagnostics() -> None:
    """Scene v2 必须按 Definition 分组、附带绑定详情，且孤儿绑定单独出现在诊断里。"""

    from src.app.workline.installed_plugin import InstalledWorkLinePlugin
    from src.app.workline.services.plane_service import WorkLinePlaneService

    installed_plugin = InstalledWorkLinePlugin(definition=_manual_picking_definition())
    workline = SimpleNamespace(
        id=7,
        version=3,
        line_code="WL-7",
        line_name="拣选一号线",
        line_type="MANUAL",
        is_active=True,
        run_mode="NORMAL",
        config={
            "device_bindings": {"SCAN1": "DEV-001", "SCAN_LEGACY": "DEV-099"},
            "position_bindings": {},
        },
    )
    device = SimpleNamespace(device_code="DEV-001", device_name="扫码枪 A", is_active=True)

    scene = WorkLinePlaneService().build_scene_v2(workline, installed_plugin, devices={"DEV-001": device}, positions={})

    assert scene.schema_version == "plane.scene.v2"
    assert scene.workline.plugin_key == "manual-picking"
    assert scene.generated_from.workline_version == 3

    device_row = scene.resource_groups.DEVICE_ROLE[0]
    assert device_row.key == "SCAN1"
    assert device_row.binding_state.value == "BOUND"
    assert device_row.binding is not None
    assert device_row.binding.code == "DEV-001"
    assert device_row.binding.name == "扫码枪 A"

    position_row = scene.resource_groups.POSITION_SLOT[0]
    assert position_row.key == "FIVE_RACK"
    assert position_row.binding_state.value == "UNBOUND"
    assert position_row.declared_constraints["allowed_rack_kind"] == "FIVE_LAYER"

    assert len(scene.diagnostics.orphan_bindings) == 1
    orphan = scene.diagnostics.orphan_bindings[0]
    assert orphan.key == "SCAN_LEGACY"
    assert orphan.bound_code == "DEV-099"
    assert orphan.group.value == "DEVICE_ROLE"


def test_build_scene_v2_marks_missing_bound_resource_invalid() -> None:
    """绑定编码找不到实际资源时必须标记 INVALID，不得伪造启用状态。"""

    from src.app.workline.installed_plugin import InstalledWorkLinePlugin
    from src.app.workline.services.plane_service import WorkLinePlaneService

    workline = SimpleNamespace(
        id=7,
        version=3,
        line_code="WL-7",
        line_name="拣选一号线",
        line_type="MANUAL",
        is_active=True,
        run_mode="NORMAL",
        config={"device_bindings": {"SCAN1": "DEV-MISSING"}},
    )

    scene = WorkLinePlaneService().build_scene_v2(
        workline,
        InstalledWorkLinePlugin(definition=_manual_picking_definition()),
    )

    resource = scene.resource_groups.DEVICE_ROLE[0]
    assert resource.binding_state.value == "INVALID"
    assert resource.binding is not None
    assert resource.binding.enabled is False


@pytest.mark.asyncio
async def test_get_scene_v2_rejects_workline_without_installed_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """未装配业务插件的 WorkLine 无法生成 Scene v2，必须显式失败而非返回空场景。"""

    from src.app.workline.services.plane_service import PlaneReadPrincipal, WorkLinePlaneService

    service = WorkLinePlaneService()
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                line_code="WL-7",
                line_name="Line 7",
                config={},
                created_by=42,
                plugin_key=None,
                plugin_version=None,
            )
        ),
    )

    with pytest.raises(ValueError, match="未装配业务插件"):
        await service.get_scene_v2(
            object(),
            object(),
            7,
            principal=PlaneReadPrincipal(user_id=42, is_superuser=False),
            plugins=(),
        )


@pytest.mark.asyncio
async def test_get_scene_v2_rejects_plugin_not_installed_in_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """WorkLine 冻结的插件身份在当前部署里找不到时必须失败关闭，不能回退到其他版本。"""

    from src.app.workline.services.plane_service import PlaneReadPrincipal, WorkLinePlaneService

    service = WorkLinePlaneService()
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                line_code="WL-7",
                line_name="Line 7",
                config={},
                created_by=42,
                plugin_key="manual-picking",
                plugin_version="9.9.9",
            )
        ),
    )

    with pytest.raises(ValueError, match="not installed"):
        await service.get_scene_v2(
            object(),
            object(),
            7,
            principal=PlaneReadPrincipal(user_id=42, is_superuser=False),
            plugins=(),
        )


@pytest.mark.asyncio
async def test_get_scene_v2_resolves_bindings_through_bulk_repository_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_scene_v2 必须批量读取工作线设备和位置，不按绑定数量产生 N+1。"""

    from src.app.workline.installed_plugin import InstalledWorkLinePlugin
    from src.app.workline.services.plane_service import PlaneReadPrincipal, WorkLinePlaneService

    installed_plugin = InstalledWorkLinePlugin(definition=_manual_picking_definition())

    class _DeviceRepoStub:
        def __init__(self) -> None:
            self.requested_workline_ids: list[int] = []

        async def get_by_work_line_id(self, _db: object, workline_id: int) -> list[object]:
            self.requested_workline_ids.append(workline_id)
            return [SimpleNamespace(device_code="DEV-001", device_name="扫码枪 A", is_active=True)]

    class _PositionRepoStub:
        async def list_for_workline(self, _db: object, _workline_id: int) -> list[object]:
            return []

    device_repository = _DeviceRepoStub()
    service = WorkLinePlaneService(device_repository=device_repository, position_repository=_PositionRepoStub())
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                version=1,
                line_code="WL-7",
                line_name="Line 7",
                line_type="MANUAL",
                is_active=True,
                run_mode="NORMAL",
                created_by=42,
                plugin_key="manual-picking",
                plugin_version="1.0.0",
                config={"device_bindings": {"SCAN1": "DEV-001"}},
            )
        ),
    )

    scene = await service.get_scene_v2(
        object(),
        object(),
        7,
        principal=PlaneReadPrincipal(user_id=42, is_superuser=False),
        plugins=(installed_plugin,),
    )

    assert device_repository.requested_workline_ids == [7]
    device_row = scene.resource_groups.DEVICE_ROLE[0]
    assert device_row.binding is not None
    assert device_row.binding.code == "DEV-001"
    assert device_row.binding.name == "扫码枪 A"


# --- plane.snapshot.v2 -------------------------------------------------------


def test_reverse_bound_codes_only_includes_bound_entries() -> None:
    """反查表只应包含 BOUND 行；UNBOUND/INVALID 不能参与资源关联匹配。"""

    from wes_plugin_sdk import WorkLineDeviceRole

    from src.app.workline.services.plane_scene_binding import classify_device_bindings, reverse_bound_codes

    roles = (
        WorkLineDeviceRole(role_key="SCAN1", display_name="扫码枪 1"),
        WorkLineDeviceRole(role_key="SCAN2", display_name="扫码枪 2"),
    )
    classified = classify_device_bindings({"device_bindings": {"SCAN1": "DEV-001", "SCAN2": ""}}, roles)

    assert reverse_bound_codes(classified) == {"DEV-001": "SCAN1"}


def test_resolve_resource_ref_prefers_position_match_and_falls_back_to_device() -> None:
    """位置命中优先于设备命中；两者都不命中时返回 None，不做任何猜测。"""

    from src.app.workline.services.plane_scene_binding import resolve_resource_ref

    position_by_code = {"POS-001": "FIVE_RACK"}
    device_by_code = {"DEV-001": "SCAN1"}

    assert resolve_resource_ref(
        location_code="POS-001",
        device_codes=["DEV-001"],
        position_by_code=position_by_code,
        device_by_code=device_by_code,
    ) == ("POSITION_SLOT", "FIVE_RACK")
    assert resolve_resource_ref(
        location_code=None,
        device_codes=["DEV-001"],
        position_by_code=position_by_code,
        device_by_code=device_by_code,
    ) == ("DEVICE_ROLE", "SCAN1")
    assert (
        resolve_resource_ref(
            location_code="POS-UNKNOWN",
            device_codes=["DEV-UNKNOWN"],
            position_by_code=position_by_code,
            device_by_code=device_by_code,
        )
        is None
    )


def _active_object(
    *,
    conflict_state: str = "OK",
    location_code: str | None = None,
    device_codes: list[str] | None = None,
) -> object:
    from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
        WorklineActiveObjectConflictState,
        WorklineActiveObjectLocationView,
        WorklineActiveObjectView,
    )

    location_summary = (
        WorklineActiveObjectLocationView(
            location_scope="WORKLINE_POSITION",
            location_code=location_code,
            conflict_state=WorklineActiveObjectConflictState.OK,
        )
        if location_code
        else None
    )
    result = WorklineActiveObjectView(
        object_type="BIN_RESOURCE",
        object_key="BIN-1",
        conflict_state=WorklineActiveObjectConflictState(conflict_state),
        location_summary=location_summary,
    )
    result._device_codes = device_codes or []
    return result


def test_build_snapshot_v2_aggregates_counts_and_highest_conflict_state_per_resource() -> None:
    """resource_states 必须覆盖全部声明资源（含零活动），未映射对象计入 unmapped_object_count。"""

    from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
        WorklineActiveObjectsResponse,
    )
    from src.app.workline.models import PlaneResourceGroup
    from src.app.workline.services.plane_service import WorkLinePlaneService

    declared_refs = [
        (PlaneResourceGroup.POSITION_SLOT, "FIVE_RACK"),
        (PlaneResourceGroup.DEVICE_ROLE, "SCAN1"),
    ]
    active_objects = WorklineActiveObjectsResponse(
        workline_id=7,
        objects=[
            _active_object(conflict_state="OK", location_code="POS-001"),
            _active_object(conflict_state="RECONCILING", location_code="POS-001"),
            _active_object(conflict_state="TRANSIENT", device_codes=["DEV-001"]),
            _active_object(conflict_state="OK", location_code="POS-UNMAPPED"),
        ],
        truncated=True,
        total_count=4,
    )

    snapshot = WorkLinePlaneService.build_snapshot_v2(
        scene_revision="abc123",
        declared_refs=declared_refs,
        active_objects=active_objects,
        device_by_code={"DEV-001": "SCAN1"},
        position_by_code={"POS-001": "FIVE_RACK"},
    )

    assert snapshot.schema_version == "plane.snapshot.v2"
    assert snapshot.scene_revision == "abc123"
    assert snapshot.source_status.value == "COMPLETE"
    assert snapshot.truncated is True
    assert snapshot.total_count == 4
    assert snapshot.unmapped_object_count == 1

    by_key = {(state.resource_ref.group.value, state.resource_ref.key): state for state in snapshot.resource_states}
    assert by_key[("POSITION_SLOT", "FIVE_RACK")].active_object_count == 2
    assert by_key[("POSITION_SLOT", "FIVE_RACK")].highest_conflict_state == "RECONCILING"
    assert by_key[("DEVICE_ROLE", "SCAN1")].active_object_count == 1
    assert by_key[("DEVICE_ROLE", "SCAN1")].highest_conflict_state == "TRANSIENT"


def test_build_current_task_v2_maps_public_fields_without_internal_evidence_ids() -> None:
    from src.app.workline.services.plane_service import WorkLinePlaneService

    result = WorkLinePlaneService.build_current_task_v2(
        SimpleNamespace(
            task_id="TASK-1",
            status="EXECUTING",
            target_rack_id="RACK-1",
            target_rack_face="A",
            last_applied_plan_revision=2,
            initial_plan_evidence_id=101,
            last_plan_evidence_id=102,
        )
    )

    assert result.schema_version == "plane.current-task.v2"
    assert result.current_task is not None
    assert result.current_task.task_id == "TASK-1"
    assert result.current_task.status == "EXECUTING"
    assert result.current_task.target_rack_id == "RACK-1"
    assert result.current_task.target_rack_face == "A"
    assert "initial_plan_evidence_id" not in result.model_dump()
    assert "last_plan_evidence_id" not in result.model_dump()


def test_build_current_task_v2_returns_successful_empty_wrapper() -> None:
    from src.app.workline.services.plane_service import WorkLinePlaneService

    result = WorkLinePlaneService.build_current_task_v2(None)

    assert result.schema_version == "plane.current-task.v2"
    assert result.current_task is None
    assert result.generated_at is not None


@pytest.mark.asyncio
async def test_get_current_task_v2_reads_workline_active_task_without_plugin_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.workline.services.plane_service import PlaneReadPrincipal, WorkLinePlaneService

    task_repository = SimpleNamespace(
        get_active_for_workline=AsyncMock(
            return_value=SimpleNamespace(
                task_id="TASK-1",
                status="PREPARING",
                target_rack_id=None,
                target_rack_face=None,
                last_applied_plan_revision=0,
            )
        )
    )
    service = WorkLinePlaneService(picking_task_repository=task_repository)
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(return_value=SimpleNamespace(id=7, created_by=42)),
    )

    db = object()
    cache = object()
    result = await service.get_current_task_v2(
        db,
        cache,
        7,
        principal=PlaneReadPrincipal(user_id=42),
    )

    task_repository.get_active_for_workline.assert_awaited_once_with(db, 7)
    assert result.current_task is not None
    assert result.current_task.task_id == "TASK-1"
    assert result.current_task.status == "PREPARING"


@pytest.mark.asyncio
async def test_get_snapshot_v2_degrades_to_failed_when_active_object_source_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活动对象来源异常必须显式降级为 FAILED，不能悄悄返回一份看似正常的零活动响应。"""

    from src.app.workline.installed_plugin import InstalledWorkLinePlugin
    from src.app.workline.services.plane_service import PlaneReadPrincipal, WorkLinePlaneService

    class _FailingActiveObjectsService:
        async def get_active_objects(self, _db: object, *, workline_id: int) -> object:
            raise RuntimeError("boom")

    installed_plugin = InstalledWorkLinePlugin(definition=_manual_picking_definition())
    service = WorkLinePlaneService(active_objects_service=_FailingActiveObjectsService())
    db = SimpleNamespace(rollback=AsyncMock())
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                version=1,
                line_code="WL-7",
                line_name="Line 7",
                created_by=42,
                plugin_key="manual-picking",
                plugin_version="1.0.0",
                config={},
            )
        ),
    )

    snapshot = await service.get_snapshot_v2(
        db,
        object(),
        7,
        principal=PlaneReadPrincipal(user_id=42, is_superuser=False),
        plugins=(installed_plugin,),
    )

    assert snapshot.source_status.value == "FAILED"
    assert snapshot.generated_at is None
    assert snapshot.resource_states == []
    assert snapshot.scene_revision
    db.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_get_snapshot_v2_rejects_workline_without_installed_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """未装配业务插件的 WorkLine 无法生成 Snapshot v2，必须显式失败而非返回空快照。"""

    from src.app.workline.services.plane_service import PlaneReadPrincipal, WorkLinePlaneService

    service = WorkLinePlaneService()
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                line_code="WL-7",
                line_name="Line 7",
                config={},
                created_by=42,
                plugin_key=None,
                plugin_version=None,
            )
        ),
    )

    with pytest.raises(ValueError, match="未装配业务插件"):
        await service.get_snapshot_v2(
            object(),
            object(),
            7,
            principal=PlaneReadPrincipal(user_id=42, is_superuser=False),
            plugins=(),
        )


# --- Active Objects v2 -------------------------------------------------------


def test_build_active_objects_v2_attaches_resource_ref_and_keeps_unmapped_objects() -> None:
    """resource_ref 命中时附加分组归属；命中不到时保留对象身份，resource_ref 为 None。"""

    from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
        WorklineActiveObjectsResponse,
    )
    from src.app.workline.services.plane_service import WorkLinePlaneService

    active_objects = WorklineActiveObjectsResponse(
        workline_id=7,
        objects=[
            _active_object(conflict_state="OK", location_code="POS-001"),
            _active_object(conflict_state="TRANSIENT", device_codes=["DEV-001"]),
            _active_object(conflict_state="OK", location_code="POS-UNMAPPED"),
        ],
        truncated=False,
        total_count=3,
    )

    result = WorkLinePlaneService.build_active_objects_v2(
        scene_revision="abc123",
        raw=active_objects,
        device_by_code={"DEV-001": "SCAN1"},
        position_by_code={"POS-001": "FIVE_RACK"},
    )

    assert result.workline_id == 7
    assert result.scene_revision == "abc123"
    assert result.total_count == 3
    assert result.objects[0].resource_ref is not None
    assert result.objects[0].resource_ref.group.value == "POSITION_SLOT"
    assert result.objects[0].resource_ref.key == "FIVE_RACK"
    assert result.objects[1].resource_ref is not None
    assert result.objects[1].resource_ref.group.value == "DEVICE_ROLE"
    assert result.objects[1].resource_ref.key == "SCAN1"
    assert result.objects[2].resource_ref is None
    assert result.objects[2].object_type == "BIN_RESOURCE"


def test_build_active_objects_v2_returns_null_resource_ref_when_scene_revision_missing() -> None:
    """scene_revision 缺失时反查表必然为空，resource_ref 也必须全部为 None，不做猜测式回退。"""

    from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
        WorklineActiveObjectsResponse,
    )
    from src.app.workline.services.plane_service import WorkLinePlaneService

    active_objects = WorklineActiveObjectsResponse(
        workline_id=7,
        objects=[_active_object(conflict_state="OK", location_code="POS-001")],
        truncated=False,
        total_count=1,
    )

    result = WorkLinePlaneService.build_active_objects_v2(
        scene_revision=None,
        raw=active_objects,
        device_by_code={},
        position_by_code={},
    )

    assert result.scene_revision is None
    assert result.objects[0].resource_ref is None


@pytest.mark.asyncio
async def test_get_active_objects_v2_resolves_scene_revision_and_resource_ref_when_plugin_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """插件已装配且已安装时，必须解析出 scene_revision 并按绑定关联资源。"""

    from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
        WorklineActiveObjectsResponse,
    )
    from src.app.workline.installed_plugin import InstalledWorkLinePlugin
    from src.app.workline.services.plane_service import WorkLinePlaneService

    installed_plugin = InstalledWorkLinePlugin(definition=_manual_picking_definition())

    class _ActiveObjectsStub:
        async def get_active_objects(self, _db: object, *, workline_id: int) -> object:
            return WorklineActiveObjectsResponse(
                workline_id=workline_id,
                objects=[_active_object(conflict_state="OK", device_codes=["DEV-001"])],
                truncated=False,
                total_count=1,
            )

    service = WorkLinePlaneService(active_objects_service=_ActiveObjectsStub())
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                version=1,
                line_code="WL-7",
                line_name="Line 7",
                created_by=42,
                plugin_key="manual-picking",
                plugin_version="1.0.0",
                config={"device_bindings": {"SCAN1": "DEV-001"}},
            )
        ),
    )

    result = await service.get_active_objects_v2(object(), object(), 7, plugins=(installed_plugin,))

    assert result.scene_revision is not None
    assert result.objects[0].resource_ref is not None
    assert result.objects[0].resource_ref.key == "SCAN1"


@pytest.mark.asyncio
async def test_get_active_objects_v2_degrades_gracefully_without_installed_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未装配/未安装插件时不是硬失败：scene_revision 与 resource_ref 保持 None，对象身份仍返回。"""

    from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
        WorklineActiveObjectsResponse,
    )
    from src.app.workline.services.plane_service import WorkLinePlaneService

    class _ActiveObjectsStub:
        async def get_active_objects(self, _db: object, *, workline_id: int) -> object:
            return WorklineActiveObjectsResponse(
                workline_id=workline_id,
                objects=[_active_object(conflict_state="OK", location_code="POS-001")],
                truncated=False,
                total_count=1,
            )

    service = WorkLinePlaneService(active_objects_service=_ActiveObjectsStub())
    monkeypatch.setattr(
        service,
        "_load_workline",
        AsyncMock(
            return_value=SimpleNamespace(
                id=7,
                version=1,
                line_code="WL-7",
                line_name="Line 7",
                created_by=42,
                plugin_key=None,
                plugin_version=None,
                config={},
            )
        ),
    )

    result = await service.get_active_objects_v2(object(), object(), 7, plugins=())

    assert result.scene_revision is None
    assert result.objects[0].resource_ref is None
    assert result.total_count == 1
