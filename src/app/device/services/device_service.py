"""Device Service 层"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from src.app.device.models import Device, DeviceStatus, parse_device_capabilities
from src.app.device.repositories import DeviceCommandRepository, DeviceRepository, device_repository
from src.app.device.services.runtime_state_policy import DeviceRuntimeStatePolicy
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.sys.services.event_stream_service import (
    DEVICE_STATUS_CHANGED_EVENT,
    defer_sse_event,
    publish_deferred_sse_events,
)
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.exceptions import BusinessException
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.repositories import WorkLineRepository


class DeviceService(BaseService[Device, DeviceRepository]):
    """设备业务逻辑层"""

    TOPOLOGY_FIELDS = frozenset(
        {
            "device_code",
            "work_line_id",
            "device_role",
            "role_index",
            "upstream_device_id",
            "vendor_type",
            "capabilities_json",
            "sort_order",
        }
    )
    RUNTIME_FIELDS = frozenset(
        {
            "device_status",
            "current_command_id",
            "last_heartbeat_at",
            "error_code",
            "maintenance_mode",
            "max_concurrent_tasks",
        }
    )

    def __init__(self, runtime_projection_writer: Any | None = None) -> None:
        super().__init__(
            device_repository,
            enable_cache=True,
            cache_prefix=cache_settings.DEVICE.prefix,
            cache_expire=cache_settings.DEVICE.expire,
            list_cache_prefix=cache_settings.DEVICE_LIST.prefix,
            list_cache_expire=cache_settings.DEVICE_LIST.expire,
        )
        self.command_repo = DeviceCommandRepository()
        self.workline_repo: WorkLineRepository = workline_repository
        if runtime_projection_writer is None:
            from src.app.runtime.orchestration.services.device_runtime_projection_writer_service import (
                device_runtime_projection_writer_service,
            )

            runtime_projection_writer = device_runtime_projection_writer_service
        self.runtime_projection_writer = runtime_projection_writer

    @staticmethod
    def _resolve_work_line_id(device: Device | None) -> int | None:
        return getattr(device, "work_line_id", None) if device else None

    async def _commit_if_requested(self, db: "AsyncSession", *, auto_commit: bool) -> None:
        if auto_commit:
            await db.commit()
            await publish_deferred_sse_events(db)

    @staticmethod
    def _event_value(value: Any) -> Any:
        return getattr(value, "value", value)

    def _runtime_old_state(self, device: Device | None) -> dict[str, Any]:
        return {
            "device_status": getattr(device, "device_status", None),
            "current_command_id": getattr(device, "current_command_id", None),
            "error_code": getattr(device, "error_code", None),
            "maintenance_mode": getattr(device, "maintenance_mode", None),
        }

    def _runtime_state_after_update(self, device: Device, data: dict[str, Any]) -> dict[str, Any]:
        state = self._runtime_old_state(device)
        state.update(data)
        return state

    @staticmethod
    def _runtime_field_value(device: Device, key: str) -> Any:
        defaults: dict[str, Any] = {
            "device_status": DeviceStatus.IDLE,
            "maintenance_mode": False,
            "current_command_id": None,
            "error_code": None,
        }
        return getattr(device, key, defaults.get(key))

    def _defer_device_status_event(
        self,
        db: "AsyncSession",
        *,
        device: Device,
        old_state: dict[str, Any],
        changed_fields: list[str],
    ) -> None:
        runtime_fields = {"device_status", "current_command_id", "error_code", "maintenance_mode"}
        relevant_changes = [field for field in changed_fields if field in runtime_fields]
        if not relevant_changes:
            return

        defer_sse_event(
            db,
            DEVICE_STATUS_CHANGED_EVENT,
            {
                "domain": "workline_runtime",
                "entity": "device",
                "action": "updated",
                "keys": {
                    "workline_id": getattr(device, "work_line_id", None),
                    "device_id": getattr(device, "id", None),
                },
                "device_id": getattr(device, "id", None),
                "device_code": getattr(device, "device_code", None),
                "work_line_id": getattr(device, "work_line_id", None),
                "status": self._event_value(getattr(device, "device_status", None)),
                "previous_status": self._event_value(old_state.get("device_status")),
                "current_command_id": getattr(device, "current_command_id", None),
                "error_code": getattr(device, "error_code", None),
                "maintenance_mode": getattr(device, "maintenance_mode", None),
                "version": getattr(device, "version", None),
                "changed_fields": relevant_changes,
                "timestamp": timezone.now_utc().isoformat(),
            },
        )

    async def _update_runtime_state(
        self,
        db: "AsyncSession",
        device: Device,
        data: dict[str, Any],
        *,
        auto_commit: bool,
    ) -> Device | None:
        """更新设备运行态字段；只在状态变化时写库，避免设备行被任务流水持续热更新。"""

        device_id = getattr(device, "id", None)
        if not isinstance(device_id, int):
            return None

        update_data = {key: value for key, value in data.items() if self._runtime_field_value(device, key) != value}
        if not update_data:
            await self._commit_if_requested(db, auto_commit=auto_commit)
            return device

        DeviceRuntimeStatePolicy.validate(
            self._runtime_state_after_update(device, update_data),
            reason="device_runtime_update",
        )

        changed_fields = sorted(update_data)
        old_state = self._runtime_old_state(device)
        current_version = getattr(device, "version", None)
        if current_version is not None:
            update_data["version"] = current_version

        updated = await self.repo.update(db, device_id, update_data)
        if updated is not None:
            await self.runtime_projection_writer.upsert_from_device(
                db,
                device=updated,
                evidence_json={
                    "source": "device_service_runtime_update",
                    "changed_fields": changed_fields,
                },
                auto_commit=False,
            )
            self._defer_device_status_event(db, device=updated, old_state=old_state, changed_fields=changed_fields)
        await self._commit_if_requested(db, auto_commit=auto_commit)
        return updated

    async def _update_runtime_state_batch(
        self,
        db: "AsyncSession",
        devices: list[Device],
        data: dict[str, Any],
        *,
        auto_commit: bool,
    ) -> int:
        updated_count = 0
        for device in devices:
            updated = await self._update_runtime_state(db, device, data, auto_commit=False)
            if updated is not None:
                updated_count += 1

        await self._commit_if_requested(db, auto_commit=auto_commit)
        return updated_count

    async def get_device_by_code(self, db: "AsyncSession", device_code: str) -> Device | None:
        """根据 device_code 查询设备。"""
        return await self.repo.get_by_device_code(db, device_code)

    async def mark_command_dispatched(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        command_id: int,
        auto_commit: bool = True,
    ) -> Device | None:
        """设备已接收命令 ACK 后，维护 WES 侧设备占用状态。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None

        projection = DeviceRuntimeStatePolicy.running(command_id)
        DeviceRuntimeStatePolicy.validate(projection.data, reason="command_dispatched")
        return await self._update_runtime_state(db, device, projection.data, auto_commit=auto_commit)

    async def mark_command_finished(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        command_id: int,
        success: bool,
        error_code: str | None = None,
        auto_commit: bool = True,
    ) -> Device | None:
        """设备命令结果回传后，按仍未闭环的命令推导设备运行态。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None

        if self._is_maintenance_state(device):
            reason = (
                DeviceRuntimeStatePolicy.normalize_error_code(getattr(device, "error_code", None), "MAINTENANCE")
                if success
                else DeviceRuntimeStatePolicy.normalize_error_code(error_code, "COMMAND_FAILED")
            )
            update_data = DeviceRuntimeStatePolicy.maintenance(reason).data
            return await self._update_runtime_state(db, device, update_data, auto_commit=auto_commit)

        if not success:
            projection = DeviceRuntimeStatePolicy.error(error_code, fallback="COMMAND_FAILED")
            return await self._update_runtime_state(
                db,
                device,
                projection.data,
                auto_commit=auto_commit,
            )

        active_commands = await self.command_repo.get_active_commands_for_device(
            db,
            device_id,
            exclude_command_id=command_id,
            limit=1,
        )
        if not active_commands:
            return await self._update_runtime_state(
                db, device, DeviceRuntimeStatePolicy.idle().data, auto_commit=auto_commit
            )

        next_command_id = getattr(active_commands[0], "id", None)
        if not isinstance(next_command_id, int):
            return await self._update_runtime_state(
                db, device, DeviceRuntimeStatePolicy.idle().data, auto_commit=auto_commit
            )
        return await self._update_runtime_state(
            db,
            device,
            DeviceRuntimeStatePolicy.running(next_command_id).data,
            auto_commit=auto_commit,
        )

    def _is_maintenance_state(self, device: Device) -> bool:
        return bool(getattr(device, "maintenance_mode", False)) or (
            self._event_value(getattr(device, "device_status", None)) == DeviceStatus.MAINTENANCE.value
        )

    async def record_heartbeat(
        self,
        db: "AsyncSession",
        *,
        device_code: str,
        auto_commit: bool = True,
    ) -> Device | None:
        """记录设备最近一次可达时间，不隐式清除 ERROR 等人工需要关注的状态。"""

        device = await self.repo.get_by_device_code(db, device_code)
        if device is None:
            return None

        data: dict[str, Any] = {
            "last_heartbeat_at": timezone.now_for_db(),
            "device_status": getattr(device, "device_status", DeviceStatus.IDLE),
            "error_code": getattr(device, "error_code", None),
            "maintenance_mode": getattr(device, "maintenance_mode", False),
            "current_command_id": getattr(device, "current_command_id", None),
        }
        return await self._update_runtime_state(db, device, data, auto_commit=auto_commit)

    async def mark_stale_heartbeats_offline(
        self,
        db: "AsyncSession",
        *,
        threshold_seconds: int,
        limit: int = 100,
        auto_commit: bool = True,
    ) -> int:
        """将心跳超时的 IDLE/RUNNING 设备标记为 OFFLINE。"""

        cutoff = timezone.now_for_db() - timedelta(seconds=threshold_seconds)
        devices = await self.repo.get_heartbeat_stale_devices(db, cutoff=cutoff, limit=limit)
        return await self._update_runtime_state_batch(
            db,
            devices,
            DeviceRuntimeStatePolicy.offline().data,
            auto_commit=auto_commit,
        )

    async def enter_maintenance(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        reason: str | None = None,
        auto_commit: bool = True,
    ) -> Device | None:
        """进入维护态，释放当前硬件占用投影。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None
        return await self._update_runtime_state(
            db,
            device,
            DeviceRuntimeStatePolicy.maintenance(reason).data,
            auto_commit=auto_commit,
        )

    async def exit_maintenance(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        auto_commit: bool = True,
    ) -> Device | None:
        """退出维护态并回到可派发的 IDLE 投影。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None
        if not self._is_maintenance_state(device):
            return device
        return await self._update_runtime_state(
            db, device, DeviceRuntimeStatePolicy.idle().data, auto_commit=auto_commit
        )

    async def clear_fault(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        auto_commit: bool = True,
    ) -> Device | None:
        """清除设备故障投影；维护态不能通过清故障绕过。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None
        if self._is_maintenance_state(device):
            raise BusinessException("维护态设备必须先退出维护，不能通过清除故障绕过维护投影")
        if self._event_value(getattr(device, "device_status", None)) != DeviceStatus.ERROR.value:
            return device
        return await self._update_runtime_state(
            db, device, DeviceRuntimeStatePolicy.idle().data, auto_commit=auto_commit
        )

    async def mark_workline_safety_error(
        self,
        db: "AsyncSession",
        *,
        workline_id: int,
        auto_commit: bool = True,
    ) -> int:
        """将 WorkLine 下可运行设备投影为急停错误。"""

        devices = await self.repo.get_non_maintenance_by_workline_for_update(db, workline_id)
        return await self._update_runtime_state_batch(
            db,
            devices,
            DeviceRuntimeStatePolicy.error("WORKLINE_ESTOPPED").data,
            auto_commit=auto_commit,
        )

    async def mark_callback_deadline_expired(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        auto_commit: bool = True,
    ) -> Device | None:
        """执行 Callback 超时后，将受影响设备投影为需人工对账的错误态。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None
        return await self._update_runtime_state(
            db,
            device,
            DeviceRuntimeStatePolicy.callback_deadline_expired().data,
            auto_commit=auto_commit,
        )

    async def mark_dispatch_ack_exhausted(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        auto_commit: bool = True,
    ) -> Device | None:
        """派发 ACK 重试耗尽后，将设备投影为通信 ACK 对账错误态。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None
        return await self._update_runtime_state(
            db,
            device,
            DeviceRuntimeStatePolicy.dispatch_ack_exhausted().data,
            auto_commit=auto_commit,
        )

    async def clear_reconciliation_error(
        self,
        db: "AsyncSession",
        *,
        device_id: int,
        expected_error_code: str,
        auto_commit: bool = True,
    ) -> Device | None:
        """只清除当前 runtime reconciliation reason 对应的设备错误。"""

        device = await self.repo.get_by_id(db, device_id)
        if device is None:
            return None
        if self._event_value(getattr(device, "device_status", None)) != DeviceStatus.ERROR.value:
            return device
        if getattr(device, "error_code", None) != expected_error_code:
            return device
        return await self._update_runtime_state(
            db,
            device,
            DeviceRuntimeStatePolicy.idle().data,
            auto_commit=auto_commit,
        )

    async def clear_workline_safety_error(
        self,
        db: "AsyncSession",
        *,
        workline_id: int,
        auto_commit: bool = True,
    ) -> int:
        """只清除 WorkLine 急停派生的设备错误。"""

        devices = await self.repo.get_safety_error_by_workline_for_update(db, workline_id)
        return await self._update_runtime_state_batch(
            db,
            devices,
            DeviceRuntimeStatePolicy.idle().data,
            auto_commit=auto_commit,
        )

    async def create(
        self,
        db: "AsyncSession",
        data: dict[str, Any],
        cache: object | None = None,
    ) -> Device | None:
        """创建设备前校验 capability schema。"""

        await self._reject_active_workline_topology_update(db, None, data)
        self._validate_capabilities(data)
        return await super().create(db, data, cache)

    async def update(
        self,
        db: "AsyncSession",
        id: int,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> Device | None:
        """更新设备后失效工作线设备缓存（内存缓存）"""
        # 先获取旧设备信息
        old_device = await self.repo.get_by_id(db, id)
        old_work_line_id = self._resolve_work_line_id(old_device)
        old_state = self._runtime_old_state(old_device)

        self._reject_runtime_update(data)
        await self._reject_active_workline_topology_update(db, old_device, data)
        self._validate_capabilities(data, current=old_device)

        # 执行更新
        updated_device = await super().update(db, id, data, cache)

        if updated_device:
            changed_fields = self._changed_runtime_fields(updated_device, old_state, data)
            if changed_fields:
                self._defer_device_status_event(
                    db,
                    device=updated_device,
                    old_state=old_state,
                    changed_fields=changed_fields,
                )
                await publish_deferred_sse_events(db)

            new_work_line_id = self._resolve_work_line_id(updated_device)
            # 比较 work_line_id 变化，失效进程内缓存
            if old_work_line_id != new_work_line_id:
                await self.repo.after_device_change(db, old_work_line_id, new_work_line_id)

        return updated_device

    async def delete(
        self,
        db: "AsyncSession",
        id: int,
        cache: object | None = None,
    ) -> bool | None:
        """删除设备前参与 WorkLine 拓扑排他锁协议。"""

        current = await self.repo.get_by_id(db, id)
        if current is not None:
            await self._reject_active_workline_topology_update(db, current, {"work_line_id": None})
        return await super().delete(db, id, cache)

    async def soft_delete(
        self,
        db: "AsyncSession",
        id: int,
        cache: object | None = None,
    ) -> Device | None:
        """显式软删除入口同样不得绕过活动 WorkLine 拓扑锁。"""

        current = await self.repo.get_by_id(db, id)
        if current is not None:
            await self._reject_active_workline_topology_update(db, current, {"work_line_id": None})
        return await super().soft_delete(db, id, cache)

    async def permanent_delete(
        self,
        db: "AsyncSession",
        id: int,
        cache: object | None = None,
    ) -> bool:
        """永久删除前锁定设备所属 WorkLine，避免运行期拓扑幻读。"""

        current = await self.repo.get_by_id(db, id, include_deleted=True)
        if current is not None:
            await self._reject_active_workline_topology_update(db, current, {"work_line_id": None})
        return await super().permanent_delete(db, id, cache)

    async def restore(
        self,
        db: "AsyncSession",
        id: int,
        cache: object | None = None,
    ) -> Device | None:
        """恢复设备会重新加入拓扑，必须在同一排他锁协议内校验。"""

        current = await self.repo.get_by_id(db, id, include_deleted=True)
        if current is not None:
            await self._reject_active_workline_topology_update(
                db,
                current,
                {"work_line_id": current.work_line_id},
            )
        return await super().restore(db, id, cache)

    def _reject_runtime_update(self, data: dict[str, Any]) -> None:
        """普通 CRUD 不允许修改运行态字段。"""

        submitted_runtime_fields = sorted(self.RUNTIME_FIELDS.intersection(data))
        if submitted_runtime_fields:
            raise BusinessException(
                message=f"设备运行态字段只能通过专用操作修改: {', '.join(submitted_runtime_fields)}",
                detail={"fields": submitted_runtime_fields},
            )

    async def _reject_active_workline_topology_update(
        self,
        db: "AsyncSession",
        current: Device | None,
        data: dict[str, Any],
    ) -> None:
        """已启用 WorkLine 下禁止通过设备 CRUD 改变拓扑事实。"""

        submitted_topology_fields = sorted(self.TOPOLOGY_FIELDS.intersection(data))
        if not submitted_topology_fields:
            return

        affected_workline_ids: set[int] = set()
        old_workline_id = self._resolve_work_line_id(current)
        if isinstance(old_workline_id, int):
            affected_workline_ids.add(old_workline_id)
        new_workline_id = data.get("work_line_id")
        if isinstance(new_workline_id, int):
            affected_workline_ids.add(new_workline_id)

        # 与 RuntimeInbox Stage 3 使用同一 workline advisory lock；先取 advisory、
        # 再取 WorkLine 行锁，既覆盖并发插入，也保持 activation/deactivation 锁顺序一致。
        for workline_id in sorted(affected_workline_ids):
            await self.workline_repo.acquire_plugin_pin_exclusive(db, workline_id)

        # 初读只用于确定 advisory lock；所有设备拓扑写都会先取得旧/新 WorkLine 锁，
        # 因而锁后重新读取若已漂移，说明本次锁集不完整，必须安全重试而不能继续变更。
        current_id = getattr(current, "id", None)
        if isinstance(current_id, int):
            expected_identity = (old_workline_id, bool(getattr(current, "is_deleted", False)))
            authoritative_identity = await self.repo.get_topology_identity(db, current_id)
            if authoritative_identity != expected_identity:
                raise BusinessException(
                    message="设备拓扑已并发变更，请重试",
                    detail={
                        "device_id": current_id,
                        "expected_identity": expected_identity,
                        "actual_identity": authoritative_identity,
                    },
                )
        for workline_id in sorted(affected_workline_ids):
            workline = await self.workline_repo.get_for_update(db, workline_id)
            if bool(getattr(workline, "is_active", False)):
                raise BusinessException(
                    message="已启用作业线下不能修改设备拓扑字段，请先停用作业线",
                    detail={"work_line_id": workline_id, "fields": submitted_topology_fields},
                )

    def _changed_runtime_fields(
        self,
        device: Device,
        old_state: dict[str, Any],
        submitted_data: dict[str, Any],
    ) -> list[str]:
        runtime_fields = ("device_status", "current_command_id", "error_code", "maintenance_mode")
        changed_fields: list[str] = []
        for field in runtime_fields:
            if field not in submitted_data:
                continue
            if self._event_value(old_state.get(field)) != self._event_value(getattr(device, field, None)):
                changed_fields.append(field)
        return changed_fields

    @staticmethod
    def _validate_capabilities(data: dict[str, Any], current: Device | None = None) -> None:
        """校验设备能力声明结构，保持 schema 轻量且稳定。"""

        if "capabilities_json" not in data and current is None:
            return

        raw_value = data.get("capabilities_json", getattr(current, "capabilities_json", None))
        _ = parse_device_capabilities(raw_value)


# 创建单例
device_service = DeviceService()
