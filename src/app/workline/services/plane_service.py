"""WorkLine plane scene/snapshot read service."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from src.app.device.repositories.device_repository import device_repository
from src.app.runtime.orchestration.repositories.workline_position_repository import (
    workline_position_repository,
)
from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
    workline_active_objects_service,
)
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services.audit_service import audit_log_service
from src.app.wms_integration.outbound_picking.repositories import (
    PickingTaskRepository,
    picking_task_repository,
)
from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin_version
from src.app.workline.models import (
    PlaneActiveObjectLocation,
    PlaneActiveObjectsV2,
    PlaneActiveObjectView,
    PlaneCurrentTaskV2,
    PlaneCurrentTaskView,
    PlaneNode,
    PlaneOrphanBinding,
    PlaneResource,
    PlaneResourceBinding,
    PlaneResourceGroup,
    PlaneResourceGroups,
    PlaneResourceRef,
    PlaneResourceState,
    PlaneSceneDiagnostics,
    PlaneSceneGeneratedFrom,
    PlaneSceneV2,
    PlaneSceneView,
    PlaneSnapshot,
    PlaneSnapshotSourceStatus,
    PlaneSnapshotV2,
    PlaneWorkLineIdentityV2,
    SceneBindingState,
    WorkLine,
)
from src.app.workline.services.plane_scene_binding import (
    ClassifiedBinding,
    classify_device_bindings,
    classify_position_bindings,
    resolve_resource_ref,
    reverse_bound_codes,
)
from src.app.workline.services.workline_service import workline_service
from src.core.exceptions import PermissionException
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.runtime.orchestration.services.query.workline_active_objects_service import (
        WorklineActiveObjectsResponse,
    )


@dataclass(frozen=True, slots=True)
class PlaneReadPrincipal:
    """WorkLine plane read 当前读者上下文。"""

    user_id: int
    is_superuser: bool = False


@dataclass(frozen=True, slots=True)
class PlaneReadSecurityPolicy:
    """WorkLine plane read 安全合同。"""

    scene_permission: str = "biz:workline:view-plane-scene"
    snapshot_permission: str = "biz:workline:view-plane-snapshot"
    scope: str = "WORKLINE_LOCAL"
    scene_audit_action: str = "WORKLINE_PLANE_SCENE_READ"
    snapshot_audit_action: str = "WORKLINE_PLANE_SNAPSHOT_READ"
    current_task_audit_action: str = "WORKLINE_PLANE_CURRENT_TASK_READ"
    redacted_workline_fields: frozenset[str] = frozenset(
        {
            "config",
            "runtime_config_json",
            "diagnostic_profile",
            "description",
        }
    )

    def permission_for(self, view: Literal["scene", "snapshot", "current_task"]) -> str:
        if view == "scene":
            return self.scene_permission
        return self.snapshot_permission

    def can_read_workline(self, workline: Any, principal: PlaneReadPrincipal) -> bool:
        if principal.is_superuser:
            return True
        return getattr(workline, "created_by", None) == principal.user_id

    def ensure_can_read_workline(self, workline: Any, principal: PlaneReadPrincipal) -> None:
        if self.can_read_workline(workline, principal):
            return
        raise PermissionException(
            "无权读取该 WorkLine plane 视图",
            detail={
                "scope": self.scope,
                "workline_id": str(getattr(workline, "id", "")),
                "viewer_user_id": str(principal.user_id),
            },
        )

    def audit_event(
        self,
        view: Literal["scene", "snapshot", "current_task"],
        *,
        workline_id: int,
        workline_code: str,
    ) -> dict[str, str]:
        action = {
            "scene": self.scene_audit_action,
            "snapshot": self.snapshot_audit_action,
            "current_task": self.current_task_audit_action,
        }[view]
        return {
            "action": action,
            "permission": self.permission_for(view),
            "scope": self.scope,
            "workline_id": str(workline_id),
            "workline_code": workline_code,
        }


plane_read_security_policy = PlaneReadSecurityPolicy()


class WorkLinePlaneService:
    """WorkLine 平面态势读模型服务。"""

    def __init__(
        self,
        *,
        audit_service: Any = audit_log_service,
        security_policy: PlaneReadSecurityPolicy = plane_read_security_policy,
        device_repository: Any = device_repository,
        position_repository: Any = workline_position_repository,
        active_objects_service: Any = workline_active_objects_service,
        picking_task_repository: PickingTaskRepository | Any = picking_task_repository,
    ) -> None:
        self.audit_service = audit_service
        self.security_policy = security_policy
        self._devices = device_repository
        self._positions = position_repository
        self._active_objects = active_objects_service
        self._picking_tasks = picking_task_repository

    async def get_scene(
        self,
        db: AsyncSession,
        cache: Any,
        workline_id: int,
        *,
        principal: PlaneReadPrincipal,
    ) -> PlaneSceneView:
        """读取 WorkLine 静态平面 scene。"""

        workline = await self._load_workline(db, cache, workline_id)
        self.security_policy.ensure_can_read_workline(workline, principal)
        return self.build_scene(workline)

    async def get_snapshot(
        self,
        db: AsyncSession,
        cache: Any,
        workline_id: int,
        *,
        principal: PlaneReadPrincipal,
    ) -> PlaneSnapshot:
        """读取 WorkLine 动态平面 snapshot。"""

        workline = await self._load_workline(db, cache, workline_id)
        self.security_policy.ensure_can_read_workline(workline, principal)
        return self.build_snapshot(workline)

    async def get_scene_v2(
        self,
        db: AsyncSession,
        cache: Any,
        workline_id: int,
        *,
        principal: PlaneReadPrincipal,
        plugins: tuple[InstalledWorkLinePlugin, ...],
    ) -> PlaneSceneV2:
        """读取资源中心 WorkLine plane scene v2；与 plane.scene.v1 并存。"""

        workline = await self._load_workline(db, cache, workline_id)
        self.security_policy.ensure_can_read_workline(workline, principal)

        if not workline.plugin_key or not workline.plugin_version:
            raise ValueError(f"作业线未装配业务插件，无法生成 Scene v2: {workline_id}")
        try:
            installed_plugin = resolve_installed_plugin_version(plugins, workline.plugin_key, workline.plugin_version)
        except LookupError as exc:
            raise ValueError(str(exc)) from exc

        devices: dict[str, Any] = {
            device.device_code: device for device in await self._devices.get_by_work_line_id(db, workline_id)
        }
        positions: dict[str, Any] = {
            position.position_code: position for position in await self._positions.list_for_workline(db, workline_id)
        }

        return self.build_scene_v2(workline, installed_plugin, devices=devices, positions=positions)

    async def get_snapshot_v2(
        self,
        db: AsyncSession,
        cache: Any,
        workline_id: int,
        *,
        principal: PlaneReadPrincipal,
        plugins: tuple[InstalledWorkLinePlugin, ...],
    ) -> PlaneSnapshotV2:
        """读取资源中心 WorkLine plane snapshot v2；按 Scene 资源聚合活动数量与最高冲突状态。"""

        workline = await self._load_workline(db, cache, workline_id)
        self.security_policy.ensure_can_read_workline(workline, principal)

        if not workline.plugin_key or not workline.plugin_version:
            raise ValueError(f"作业线未装配业务插件，无法生成 Snapshot v2: {workline_id}")
        try:
            installed_plugin = resolve_installed_plugin_version(plugins, workline.plugin_key, workline.plugin_version)
        except LookupError as exc:
            raise ValueError(str(exc)) from exc

        definition = installed_plugin.definition
        config = workline.config
        device_by_code = reverse_bound_codes(classify_device_bindings(config, definition.device_roles))
        position_by_code = reverse_bound_codes(classify_position_bindings(config, definition.position_slots))
        scene_revision = self.compute_scene_revision(
            workline_id=workline.id,
            workline_version=workline.version,
            plugin_key=definition.plugin_key,
            plugin_version=definition.plugin_version,
            resource_signature=repr((definition.device_roles, definition.position_slots)),
        )
        declared_refs = [(PlaneResourceGroup.POSITION_SLOT, slot.slot_key) for slot in definition.position_slots] + [
            (PlaneResourceGroup.DEVICE_ROLE, role.role_key) for role in definition.device_roles
        ]

        try:
            # Active Objects 是 Snapshot 唯一的动态来源；查询失败必须显式降级为 FAILED，
            # 不得把异常吞掉后返回一份看起来正常的零活动响应（§5.2 COMPLETE 零值语义）。
            active_objects = await self._active_objects.get_active_objects(db, workline_id=workline_id)
        except Exception:
            await db.rollback()
            return PlaneSnapshotV2(
                schema_version="plane.snapshot.v2",
                scene_revision=scene_revision,
                generated_at=None,
                source_status=PlaneSnapshotSourceStatus.FAILED,
                truncated=False,
                total_count=0,
                resource_states=[],
                unmapped_object_count=0,
            )

        return self.build_snapshot_v2(
            scene_revision=scene_revision,
            declared_refs=declared_refs,
            active_objects=active_objects,
            device_by_code=device_by_code,
            position_by_code=position_by_code,
        )

    async def get_active_objects_v2(
        self,
        db: AsyncSession,
        cache: Any,
        workline_id: int,
        *,
        plugins: tuple[InstalledWorkLinePlugin, ...],
    ) -> PlaneActiveObjectsV2:
        """读取资源中心 Active Objects v2；顶层 scene_revision + 逐对象 resource_ref。

        与 Scene/Snapshot v2 不同：未装配插件或插件未安装不是硬失败，只是让
        scene_revision 与全部 resource_ref 保持为 null（§5.3：前端据此保留对象身份、
        不归类到任何资源行），因为运行中的对象本身仍值得展示。
        """

        workline = await self._load_workline(db, cache, workline_id)
        device_by_code: dict[str, str] = {}
        position_by_code: dict[str, str] = {}
        scene_revision: str | None = None

        if workline.plugin_key and workline.plugin_version:
            try:
                installed_plugin = resolve_installed_plugin_version(
                    plugins, workline.plugin_key, workline.plugin_version
                )
            except LookupError:
                installed_plugin = None
            if installed_plugin is not None:
                definition = installed_plugin.definition
                config = workline.config
                device_by_code = reverse_bound_codes(classify_device_bindings(config, definition.device_roles))
                position_by_code = reverse_bound_codes(classify_position_bindings(config, definition.position_slots))
                scene_revision = self.compute_scene_revision(
                    workline_id=workline.id,
                    workline_version=workline.version,
                    plugin_key=definition.plugin_key,
                    plugin_version=definition.plugin_version,
                    resource_signature=repr((definition.device_roles, definition.position_slots)),
                )

        raw = await self._active_objects.get_active_objects(db, workline_id=workline_id)
        return self.build_active_objects_v2(
            scene_revision=scene_revision,
            raw=raw,
            device_by_code=device_by_code,
            position_by_code=position_by_code,
        )

    async def get_current_task_v2(
        self,
        db: AsyncSession,
        cache: Any,
        workline_id: int,
        *,
        principal: PlaneReadPrincipal,
    ) -> PlaneCurrentTaskV2:
        """按需读取 WorkLine 当前 PickingTask；不参与 snapshot 轮询。"""

        workline = await self._load_workline(db, cache, workline_id)
        self.security_policy.ensure_can_read_workline(workline, principal)
        task = await self._picking_tasks.get_active_for_workline(db, workline_id)
        return self.build_current_task_v2(task)

    @staticmethod
    def build_current_task_v2(task: Any | None) -> PlaneCurrentTaskV2:
        current_task = (
            PlaneCurrentTaskView(
                task_id=str(task.task_id),
                status=str(task.status),
                target_rack_id=task.target_rack_id,
                target_rack_face=task.target_rack_face,
                last_applied_plan_revision=task.last_applied_plan_revision,
            )
            if task is not None
            else None
        )
        return PlaneCurrentTaskV2(
            schema_version="plane.current-task.v2",
            generated_at=timezone.now_utc(),
            current_task=current_task,
        )

    async def record_read_audit(
        self,
        db: AsyncSession,
        *,
        view: Literal["scene", "snapshot", "current_task"],
        workline_id: int,
        workline_code: str,
    ) -> None:
        """记录 WorkLine plane read 审计。"""

        args = {
            **self.security_policy.audit_event(view, workline_id=workline_id, workline_code=workline_code),
            "object_type": "WorkLine",
            "object_id": str(workline_id),
            "change_summary": f"read plane {view}",
        }
        path_view = {"scene": "scene", "snapshot": "snapshot", "current_task": "current-task/v2"}[view]
        await self.audit_service.create_audit_log(
            db,
            method="GET",
            title=f"WorkLine Plane {view.title()} Read",
            path=f"/work_lines/{workline_id}/plane/{path_view}",
            args=args,
            status=OperaStatus.SUCCESS,
            code="200",
            msg="OK",
        )
        await db.commit()

    async def _load_workline(self, db: AsyncSession, cache: Any, workline_id: int) -> WorkLine:
        workline = await workline_service.get_by_id(db, cache, workline_id, max_depth=0)
        if workline is None:
            raise ValueError(f"作业线不存在: {workline_id}")
        return workline

    def build_scene(self, workline: WorkLine) -> PlaneSceneView:
        """从 WorkLine 配置派生首版 plane scene。"""

        nodes = [
            PlaneNode(
                code=workline.line_code,
                label=workline.line_name,
                kind="WORKLINE",
            )
        ]
        nodes.extend(self._build_queue_nodes(workline))
        return PlaneSceneView(
            schema_version="plane.scene.v1",
            workline_code=workline.line_code,
            nodes=nodes,
            edges=[],
        )

    @staticmethod
    def compute_scene_revision(
        *,
        workline_id: int,
        workline_version: int,
        plugin_key: str | None,
        plugin_version: str | None,
        resource_signature: str,
    ) -> str:
        """Scene v2 版本指纹；WorkLine、插件身份或 Definition 资源变化即改变。"""

        raw = f"{workline_id}:{workline_version}:{plugin_key or ''}:{plugin_version or ''}:{resource_signature}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def build_scene_v2(
        self,
        workline: WorkLine,
        installed_plugin: InstalledWorkLinePlugin,
        *,
        devices: Mapping[str, Any] = {},
        positions: Mapping[str, Any] = {},
    ) -> PlaneSceneV2:
        """从 WorkLine config + 已安装插件 Definition 组装 Scene v2；纯函数，不做 IO。"""

        definition = installed_plugin.definition
        config = workline.config

        device_bindings = classify_device_bindings(config, definition.device_roles)
        position_bindings = classify_position_bindings(config, definition.position_slots)

        device_resources = [
            self._device_resource(order, role, device_bindings.by_key[role.role_key], devices)
            for order, role in enumerate(definition.device_roles)
        ]
        position_resources = [
            self._position_resource(order, slot, position_bindings.by_key[slot.slot_key], positions)
            for order, slot in enumerate(definition.position_slots)
        ]

        orphan_bindings = [
            PlaneOrphanBinding(
                group=PlaneResourceGroup.DEVICE_ROLE, key=o.key, bound_code=o.bound_code, reason=o.reason
            )
            for o in device_bindings.orphans
        ] + [
            PlaneOrphanBinding(
                group=PlaneResourceGroup.POSITION_SLOT, key=o.key, bound_code=o.bound_code, reason=o.reason
            )
            for o in position_bindings.orphans
        ]

        line_type = workline.line_type.value if hasattr(workline.line_type, "value") else str(workline.line_type)
        run_mode = workline.run_mode.value if hasattr(workline.run_mode, "value") else str(workline.run_mode)

        return PlaneSceneV2(
            schema_version="plane.scene.v2",
            scene_revision=self.compute_scene_revision(
                workline_id=workline.id,
                workline_version=workline.version,
                plugin_key=definition.plugin_key,
                plugin_version=definition.plugin_version,
                resource_signature=repr((definition.device_roles, definition.position_slots)),
            ),
            workline=PlaneWorkLineIdentityV2(
                id=workline.id,
                version=workline.version,
                line_code=workline.line_code,
                line_name=workline.line_name,
                line_type=line_type,
                is_active=workline.is_active,
                run_mode=run_mode,
                plugin_key=definition.plugin_key,
                plugin_version=definition.plugin_version,
                plugin_display_name=definition.display_name,
            ),
            generated_from=PlaneSceneGeneratedFrom(
                workline_version=workline.version,
                plugin_version=definition.plugin_version,
            ),
            resource_groups=PlaneResourceGroups(
                POSITION_SLOT=position_resources,
                DEVICE_ROLE=device_resources,
            ),
            diagnostics=PlaneSceneDiagnostics(orphan_bindings=orphan_bindings),
        )

    @staticmethod
    def _device_resource(
        order: int,
        role: Any,
        classified: ClassifiedBinding,
        devices: Mapping[str, Any],
    ) -> PlaneResource:
        device = devices.get(classified.bound_code) if classified.bound_code else None
        binding = None
        if classified.bound_code:
            binding = PlaneResourceBinding(
                code=classified.bound_code,
                name=device.device_name if device else None,
                type=None,
                enabled=device.is_active if device else False,
            )
        binding_state = classified.state
        if binding_state == "BOUND" and device is None:
            binding_state = "INVALID"
        return PlaneResource(
            key=role.role_key,
            display_name=role.display_name,
            stable_order=order,
            declared_constraints={},
            binding=binding,
            binding_state=SceneBindingState(binding_state),
        )

    @staticmethod
    def _position_resource(
        order: int,
        slot: Any,
        classified: ClassifiedBinding,
        positions: Mapping[str, Any],
    ) -> PlaneResource:
        position = positions.get(classified.bound_code) if classified.bound_code else None
        binding = None
        if classified.bound_code:
            binding = PlaneResourceBinding(
                code=classified.bound_code,
                name=position.position_name if position else None,
                type=None,
                enabled=position.enabled if position else False,
            )
        binding_state = classified.state
        if binding_state == "BOUND" and position is None:
            binding_state = "INVALID"
        return PlaneResource(
            key=slot.slot_key,
            display_name=slot.display_name,
            stable_order=order,
            declared_constraints={
                "position_type": str(slot.position_type) if slot.position_type else None,
                "location_type": str(slot.location_type) if getattr(slot, "location_type", None) else None,
                "allowed_rack_kind": str(slot.allowed_rack_kind) if slot.allowed_rack_kind else None,
            },
            binding=binding,
            binding_state=SceneBindingState(binding_state),
        )

    def build_snapshot(self, workline: WorkLine) -> PlaneSnapshot:
        """从 active projection 派生首版 plane snapshot。

        当前先提供稳定响应壳，后续 projection assembler 接入后
        在本方法内部补 objects/extremes，不改变 API 合同。
        """

        return PlaneSnapshot(
            schema_version="plane.snapshot.v1",
            workline_code=workline.line_code,
            scene_schema_version="plane.scene.v1",
            objects=[],
            extremes=[],
        )

    @staticmethod
    def build_snapshot_v2(
        *,
        scene_revision: str,
        declared_refs: list[tuple[PlaneResourceGroup, str]],
        active_objects: WorklineActiveObjectsResponse,
        device_by_code: dict[str, str],
        position_by_code: dict[str, str],
    ) -> PlaneSnapshotV2:
        """按已解析的资源引用聚合活动对象；纯函数，不做 IO，不做猜测式关联。"""

        severity = {"OK": 0, "TRANSIENT": 1, "RECONCILING": 2}
        counts: dict[tuple[PlaneResourceGroup, str], int] = dict.fromkeys(declared_refs, 0)
        highest: dict[tuple[PlaneResourceGroup, str], str] = dict.fromkeys(declared_refs, "OK")
        unmapped = 0

        for obj in active_objects.objects:
            location_code = obj.location_summary.location_code if obj.location_summary else None
            resolved = resolve_resource_ref(
                location_code=location_code,
                device_codes=obj._device_codes,
                position_by_code=position_by_code,
                device_by_code=device_by_code,
            )
            if resolved is None:
                unmapped += 1
                continue
            ref = (PlaneResourceGroup(resolved[0]), resolved[1])
            counts[ref] += 1
            if severity[obj.conflict_state.value] > severity[highest[ref]]:
                highest[ref] = obj.conflict_state.value

        resource_states = [
            PlaneResourceState(
                resource_ref=PlaneResourceRef(group=group, key=key),
                active_object_count=counts[(group, key)],
                highest_conflict_state=highest[(group, key)],
            )
            for group, key in declared_refs
        ]

        return PlaneSnapshotV2(
            schema_version="plane.snapshot.v2",
            scene_revision=scene_revision,
            generated_at=timezone.now_utc(),
            source_status=PlaneSnapshotSourceStatus.COMPLETE,
            truncated=active_objects.truncated,
            total_count=active_objects.total_count,
            resource_states=resource_states,
            unmapped_object_count=unmapped,
        )

    @staticmethod
    def build_active_objects_v2(
        *,
        scene_revision: str | None,
        raw: WorklineActiveObjectsResponse,
        device_by_code: dict[str, str],
        position_by_code: dict[str, str],
    ) -> PlaneActiveObjectsV2:
        """按已解析的绑定反查表逐对象附加 resource_ref；纯函数，不做 IO，不做猜测式关联。"""

        objects: list[PlaneActiveObjectView] = []
        for obj in raw.objects:
            location_code = obj.location_summary.location_code if obj.location_summary else None
            resolved = resolve_resource_ref(
                location_code=location_code,
                device_codes=obj._device_codes,
                position_by_code=position_by_code,
                device_by_code=device_by_code,
            )
            resource_ref = (
                PlaneResourceRef(group=PlaneResourceGroup(resolved[0]), key=resolved[1])
                if resolved is not None
                else None
            )
            location_summary = (
                PlaneActiveObjectLocation(
                    location_scope=obj.location_summary.location_scope,
                    location_code=obj.location_summary.location_code,
                    conflict_state=obj.location_summary.conflict_state.value,
                    evidence_refs=obj.location_summary.evidence_refs,
                )
                if obj.location_summary is not None
                else None
            )
            objects.append(
                PlaneActiveObjectView(
                    object_type=obj.object_type,
                    object_key=obj.object_key,
                    conflict_state=obj.conflict_state.value,
                    primary_source=obj.primary_source,
                    all_sources=obj.all_sources,
                    operator_hint=obj.operator_hint,
                    location_summary=location_summary,
                    evidence_refs=obj.evidence_refs,
                    resource_ref=resource_ref,
                )
            )

        return PlaneActiveObjectsV2(
            workline_id=raw.workline_id,
            scene_revision=scene_revision,
            objects=objects,
            truncated=raw.truncated,
            total_count=raw.total_count,
        )

    @staticmethod
    def _build_queue_nodes(workline: WorkLine) -> list[PlaneNode]:
        config = workline.config if isinstance(workline.config, dict) else {}
        raw_queues = config.get("pipeline_queues")
        if not isinstance(raw_queues, list):
            return []

        nodes: list[PlaneNode] = []
        for item in raw_queues:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            if not isinstance(code, str) or not code.strip():
                continue
            role = item.get("role")
            nodes.append(
                PlaneNode(
                    code=code.strip(),
                    label=str(item.get("label") or code).strip(),
                    kind=str(role or "QUEUE").strip(),
                )
            )
        return nodes


workline_plane_service = WorkLinePlaneService()


__all__ = [
    "PlaneReadPrincipal",
    "PlaneReadSecurityPolicy",
    "WorkLinePlaneService",
    "plane_read_security_policy",
    "workline_plane_service",
]
