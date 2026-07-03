"""Phase 3 PlaneSceneView / PlaneSnapshot schema contract tests."""

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

    from src.app.workline.services import plane_read_security_policy

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

    from src.app.workline.services import PlaneReadPrincipal, plane_read_security_policy
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

    from src.app.workline.services import PlaneReadPrincipal, WorkLinePlaneService
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
