# 已从 workline/services/ 迁入
"""WorkLine START 准入服务。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.app.device.repositories import device_repository
from src.app.device.services.device_context_service import device_context_service
from src.app.runtime.orchestration.repositories.runtime_hold_repository import runtime_hold_repository
from src.app.runtime.orchestration.repositories.session_repository import workline_session_repository
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.runtime.orchestration.workline_runtime_status_projection import WorkLineRuntimeStatus
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.workline.repositories.safety_incident_repository import workline_safety_incident_repository
from src.app.workline.services.workline_service import workline_service
from src.core.logger import logger
from src.core.task_queue_gateway import OutboxDispatchTarget, TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SUCCESS = "SUCCESS"
_FAILED = "FAILED"
_READY = WorkLineRuntimeStatus.READY
_STOPPED = WorkLineRuntimeStatus.STOPPED
_DEFAULT_DEVICE_STATUS_PATH = "/api/v1/device/status"


@dataclass(frozen=True, slots=True)
class StartAdmissionStatusTarget:
    """单个设备的 ECS status 探测目标。"""

    scheme: str
    host: str
    port: int
    status_path: str
    device_code: str

    @property
    def url(self) -> str:
        query = urlencode({"device_code": self.device_code})
        return f"{self.scheme}://{self.host}:{self.port}{self.status_path}?{query}"


@dataclass(frozen=True, slots=True)
class StartAdmissionStatusFetchResult:
    """ECS status 探测响应快照。"""

    status_code: int
    payload: Any


@dataclass(frozen=True, slots=True)
class StartAdmissionResult:
    """START 准入结果。"""

    accepted: bool
    http_status: int
    reason_code: str | None
    message: str
    workline_id: int | None
    diagnostic: dict[str, Any]


class _UniformDeviceStatusWire(BaseModel):
    """第三方设备统一状态响应的必填包络。"""

    model_config = ConfigDict(strict=True)

    device_code: str = Field(min_length=1)
    contract_key: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    mode: Literal["AUTO", "MANUAL", "MAINTENANCE", "UNKNOWN"]
    status: Literal["IDLE", "RUNNING", "ERROR", "OFFLINE", "UNKNOWN"]
    current_command_code: str | None
    error_detail: dict[str, Any] | None
    timestamp: int


type StatusFetcher = Callable[[StartAdmissionStatusTarget, float], Awaitable[StartAdmissionStatusFetchResult]]


class WorkLineStartAdmissionService:
    """处理平台 START 事件到 READY 的准入检查。"""

    def __init__(
        self,
        *,
        status_fetcher: StatusFetcher | None = None,
        outbox_repo: SystemOutboxRepository | None = None,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
        workline_status_projection_service: Any | None = None,
    ) -> None:
        self.status_fetcher = status_fetcher or self._fetch_status
        self.outbox_repo = outbox_repo or system_outbox_repository
        self._queue_gateway = queue_gateway
        self.workline_status_projection_service = (
            workline_status_projection_service or workline_runtime_status_projection_service
        )

    async def admit_start_for_device(
        self,
        db: AsyncSession,
        device_code: str,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> StartAdmissionResult:
        """从 callback device_code 解析 WorkLine 并执行 START 准入。"""

        ctx_result, ctx_error = await device_context_service.resolve(db, device_code)
        if ctx_error:
            return self._rejected(
                None,
                "START_ADMISSION_DEVICE_CONTEXT_INVALID",
                "START 准入失败: 设备上下文解析失败",
                {"device_code": device_code, "context_error": ctx_error},
            )
        workline_id = getattr(ctx_result, "work_line_id", None)
        if not isinstance(workline_id, int):
            return self._rejected(
                None,
                "START_ADMISSION_WORKLINE_NOT_BOUND",
                "START 准入失败: 设备未绑定 WorkLine",
                {"device_code": device_code},
            )
        return await self.admit_start(
            db,
            workline_id,
            source_device_code=device_code,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def admit_start(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        source_device_code: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> StartAdmissionResult:
        """执行 WorkLine START 准入。"""

        snapshot = await self._load_start_snapshot(db, workline_id, request_id=request_id, trace_id=trace_id)
        if isinstance(snapshot, StartAdmissionResult):
            return snapshot
        workline, target_devices, targets = snapshot
        runtime_config = dict(getattr(workline, "runtime_config_json", None) or {})
        timeout_seconds = self.resolve_status_timeout_seconds(runtime_config)
        batch_concurrency = self.resolve_batch_concurrency(runtime_config)

        await db.commit()

        probe_result, status_by_device_code = await self._probe_targets(
            targets,
            timeout_seconds=timeout_seconds,
            batch_concurrency=batch_concurrency,
        )
        if probe_result is not None:
            return await self._record_post_probe_failure(
                db,
                workline_id,
                probe_result,
                request_id=request_id,
                trace_id=trace_id,
            )

        status_result = self._validate_required_device_status(target_devices, targets, status_by_device_code)
        if status_result is not None:
            return await self._record_post_probe_failure(
                db,
                workline_id,
                status_result,
                request_id=request_id,
                trace_id=trace_id,
            )

        current = await workline_repository.get_for_update(db, workline_id, populate_existing=True)
        if current is None:
            return self._rejected(
                workline_id,
                "START_ADMISSION_WORKLINE_NOT_FOUND",
                "START 准入失败: WorkLine 不存在",
                {"workline_id": workline_id},
            )
        if await self._is_ready(db, workline_id):
            return self._ready_idempotent_result(
                workline_id,
                source_device_code=source_device_code,
                checked_devices=[getattr(device, "device_code", None) for device in target_devices],
            )
        final_guard = await self._guard_startable(db, current)
        if final_guard is not None:
            runtime_status = await self._runtime_status(db, workline_id)
            await self._record_failure(
                db,
                current,
                message=final_guard,
                diagnostic={"workline_id": workline_id, "runtime_status": runtime_status},
                request_id=request_id,
                trace_id=trace_id,
            )
            return self._rejected(
                workline_id,
                "START_ADMISSION_STATE_CHANGED",
                final_guard,
                {"workline_id": workline_id, "runtime_status": runtime_status},
            )

        await self._record_success(
            db,
            current,
            request_id=request_id,
            trace_id=trace_id,
        )
        return StartAdmissionResult(
            accepted=True,
            http_status=200,
            reason_code=None,
            message="START 准入通过",
            workline_id=workline_id,
            diagnostic={
                "source_device_code": source_device_code,
                "checked_devices": [getattr(device, "device_code", None) for device in target_devices],
            },
        )

    async def _load_start_snapshot(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        request_id: str | None,
        trace_id: str | None,
    ) -> tuple[Any, list[Any], list[StartAdmissionStatusTarget]] | StartAdmissionResult:
        workline = await workline_repository.get_for_update(db, workline_id)
        if workline is None:
            return self._rejected(
                workline_id,
                "START_ADMISSION_WORKLINE_NOT_FOUND",
                "START 准入失败: WorkLine 不存在",
                {"workline_id": workline_id},
            )
        if await self._is_ready(db, workline_id):
            return self._ready_idempotent_result(workline_id)

        guard_message = await self._guard_startable(db, workline)
        if guard_message is not None:
            runtime_status = await self._runtime_status(db, workline_id)
            await self._record_failure(
                db,
                workline,
                message=guard_message,
                diagnostic={"workline_id": workline_id, "runtime_status": runtime_status},
                request_id=request_id,
                trace_id=trace_id,
            )
            return self._rejected(
                workline_id,
                "START_ADMISSION_NOT_STARTABLE",
                guard_message,
                {"workline_id": workline_id, "runtime_status": runtime_status},
            )

        configuration_status = await workline_service.configuration_status(db, workline_id)
        checks = configuration_status.checks
        blockers = [check for check in checks if check.status == "FAIL" and check.severity == "BLOCKER"]
        if blockers:
            diagnostic = {
                "checks": [check.model_dump() for check in blockers],
                "device_code": self._first_check_device_code(blockers),
            }
            await self._record_failure(
                db,
                workline,
                message="START 准入失败: 配置预检未通过",
                diagnostic=diagnostic,
                request_id=request_id,
                trace_id=trace_id,
            )
            return self._rejected(
                workline_id,
                "START_ADMISSION_CONFIGURATION_INVALID",
                "START 准入失败: 配置预检未通过",
                diagnostic,
            )

        devices = await device_repository.get_by_work_line_id(db, workline_id)
        target_devices = self._resolve_command_target_devices(workline, devices)
        if not target_devices:
            return self._rejected(
                workline_id,
                "START_ADMISSION_NO_TARGET_DEVICES",
                "START 准入失败: 未找到命令目标设备",
                {"workline_id": workline_id},
            )
        return workline, target_devices, self._build_status_targets(target_devices)

    async def _guard_startable(self, db: AsyncSession, workline: Any) -> str | None:
        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            return "START 准入失败: WorkLine ID 无效"
        if not bool(getattr(workline, "is_active", False)):
            return "START 准入失败: WorkLine 未启用"
        runtime_snapshot = await self.workline_status_projection_service.runtime_status_snapshot(
            db,
            workline_id=workline_id,
        )
        runtime_status = runtime_snapshot.runtime_status
        if runtime_status != _STOPPED.value:
            return f"START 准入失败: WorkLine 当前运行态不是 STOPPED: {runtime_status}"
        if runtime_snapshot.active_safety_incident_id is not None:
            return "START 准入失败: WorkLine 存在 active safety incident"
        active_incident = await workline_safety_incident_repository.get_active_for_workline(db, workline_id)
        if active_incident is not None:
            return "START 准入失败: WorkLine 存在 active safety incident"
        if await runtime_hold_repository.count_active_by_workline(db, workline_id):
            return "START 准入失败: WorkLine 存在 active runtime hold"
        if await workline_session_repository.count_pending_reconciliations_for_workline(db, workline_id):
            return "START 准入失败: WorkLine 存在 pending runtime reconciliation"
        return None

    def _resolve_command_target_devices(self, workline: Any, devices: list[Any]) -> list[Any]:
        """START 只依赖 WorkLine 物理拓扑，不从业务插件推导目标设备。"""

        del workline
        return sorted(
            (
                device
                for device in devices
                if isinstance(getattr(device, "id", None), int) and bool(getattr(device, "is_active", True))
            ),
            key=lambda device: (str(getattr(device, "device_code", "")), int(getattr(device, "id", 0))),
        )

    def _build_status_targets(self, devices: list[Any]) -> list[StartAdmissionStatusTarget]:
        targets: list[StartAdmissionStatusTarget] = []
        for device in devices:
            device_code = getattr(device, "device_code", None)
            host = getattr(device, "host", None)
            port = getattr(device, "port", None)
            status_path = self._resolve_device_status_path(device)
            if not isinstance(device_code, str) or not device_code or not isinstance(host, str) or not status_path:
                continue
            if not isinstance(port, int):
                continue
            targets.append(
                StartAdmissionStatusTarget(
                    scheme=self._resolve_device_scheme(device),
                    host=host,
                    port=port,
                    status_path=status_path,
                    device_code=device_code,
                )
            )
        return targets

    @staticmethod
    def _resolve_device_scheme(device: Any) -> str:
        protocol = getattr(device, "protocol", "HTTP")
        value = str(getattr(protocol, "value", protocol) or "HTTP").lower()
        return value if value in {"http", "https"} else "http"

    @staticmethod
    def _resolve_device_status_path(device: Any) -> str:
        capabilities = getattr(device, "capabilities_json", None)
        if isinstance(capabilities, dict):
            for key in ("status_path", "device_status_path"):
                value = capabilities.get(key)
                if isinstance(value, str) and value.strip():
                    path = value.strip()
                    return path if path.startswith("/") else f"/{path}"
        return _DEFAULT_DEVICE_STATUS_PATH

    async def _probe_targets(
        self,
        targets: list[StartAdmissionStatusTarget],
        *,
        timeout_seconds: float,
        batch_concurrency: int,
    ) -> tuple[StartAdmissionResult | None, dict[str, dict[str, Any]]]:
        semaphore = asyncio.Semaphore(batch_concurrency)
        status_by_device_code: dict[str, dict[str, Any]] = {}

        async def probe(target: StartAdmissionStatusTarget) -> StartAdmissionResult | None:
            async with semaphore:
                try:
                    response = await self.status_fetcher(target, timeout_seconds)
                except (TimeoutError, httpx.TimeoutException) as exc:
                    return self._rejected(
                        None,
                        "START_ADMISSION_ECS_TIMEOUT",
                        f"START 准入失败: ECS status 查询超时: {target.url}",
                        self._target_failure_diagnostic(target, {"error": str(exc)}),
                    )
                except httpx.HTTPError as exc:
                    return self._rejected(
                        None,
                        "START_ADMISSION_ECS_HTTP_ERROR",
                        f"START 准入失败: ECS status 查询失败: {target.url}",
                        self._target_failure_diagnostic(target, {"error": str(exc)}),
                    )
                except ValueError as exc:
                    return self._rejected(
                        None,
                        "START_ADMISSION_ECS_BAD_JSON",
                        "START 准入失败: ECS status 响应格式无效",
                        self._target_failure_diagnostic(target, {"error": str(exc)}),
                    )
                if not 200 <= response.status_code < 300:
                    return self._rejected(
                        None,
                        "START_ADMISSION_ECS_HTTP_ERROR",
                        f"START 准入失败: ECS status 返回非 2xx: {response.status_code}",
                        self._target_failure_diagnostic(target, {"status_code": response.status_code}),
                    )
                records = self._extract_status_records(response.payload)
                if records is None:
                    return self._rejected(
                        None,
                        "START_ADMISSION_ECS_BAD_JSON",
                        "START 准入失败: ECS status 响应格式无效",
                        self._target_failure_diagnostic(target),
                    )
                for record in records:
                    device_code = self._record_device_code(record)
                    if device_code != target.device_code:
                        if device_code is None:
                            message = "START 准入失败: ECS status 响应缺少 device_code"
                            diagnostic = {"record": record}
                        else:
                            message = "START 准入失败: ECS status 响应设备与查询目标不匹配"
                            diagnostic = {"response_device_code": device_code}
                        return self._rejected(
                            None,
                            "START_ADMISSION_ECS_BAD_JSON",
                            message,
                            self._target_failure_diagnostic(target, diagnostic),
                        )
                    if device_code in status_by_device_code:
                        return self._rejected(
                            None,
                            "START_ADMISSION_ECS_BAD_JSON",
                            "START 准入失败: ECS status 响应存在重复 device_code",
                            self._target_failure_diagnostic(target, {"device_code": device_code}),
                        )
                    status_by_device_code[device_code] = record
                return None

        results = await asyncio.gather(*(probe(target) for target in targets))
        failure = next((result for result in results if result is not None), None)
        if failure is not None:
            return failure, status_by_device_code
        for target in targets:
            if target.device_code not in status_by_device_code:
                return (
                    self._rejected(
                        None,
                        "START_ADMISSION_DEVICE_STATUS_MISSING",
                        f"START 准入失败: ECS status 未返回设备 {target.device_code}",
                        self._target_failure_diagnostic(target),
                    ),
                    status_by_device_code,
                )
        return None, status_by_device_code

    @staticmethod
    def _record_device_code(record: dict[str, Any]) -> str | None:
        for source in (record, record.get("device"), record.get("state")):
            if not isinstance(source, dict):
                continue
            device_code = source.get("device_code")
            if isinstance(device_code, str) and device_code:
                return device_code
        return None

    async def _record_post_probe_failure(
        self,
        db: AsyncSession,
        workline_id: int,
        failure: StartAdmissionResult,
        *,
        request_id: str | None,
        trace_id: str | None,
    ) -> StartAdmissionResult:
        current = await workline_repository.get_for_update(db, workline_id, populate_existing=True)
        if current is None:
            return self._rejected(
                workline_id,
                "START_ADMISSION_WORKLINE_NOT_FOUND",
                "START 准入失败: WorkLine 不存在",
                {"workline_id": workline_id},
            )

        if await self._is_ready(db, workline_id):
            return self._ready_idempotent_result(workline_id)

        final_guard = await self._guard_startable(db, current)
        if final_guard is not None:
            diagnostic = {"workline_id": workline_id, "runtime_status": await self._runtime_status(db, workline_id)}
            await self._record_failure(
                db,
                current,
                message=final_guard,
                diagnostic=diagnostic,
                request_id=request_id,
                trace_id=trace_id,
            )
            return self._rejected(
                workline_id,
                "START_ADMISSION_STATE_CHANGED",
                final_guard,
                diagnostic,
            )

        await self._record_failure(
            db,
            current,
            message=failure.message,
            diagnostic=failure.diagnostic,
            request_id=request_id,
            trace_id=trace_id,
        )
        return failure

    def _validate_required_device_status(
        self,
        devices: list[Any],
        targets: list[StartAdmissionStatusTarget],
        status_by_device_code: dict[str, dict[str, Any]],
    ) -> StartAdmissionResult | None:
        target_urls = {target.device_code: target.url for target in targets}
        for device in sorted(devices, key=lambda item: str(getattr(item, "device_code", ""))):
            device_code = getattr(device, "device_code", None)
            if not isinstance(device_code, str):
                continue
            record = status_by_device_code.get(device_code)
            if record is None:
                return self._rejected(
                    getattr(device, "work_line_id", None),
                    "START_ADMISSION_DEVICE_STATUS_MISSING",
                    f"START 准入失败: ECS status 未返回设备 {device_code}",
                    {
                        "device_code": device_code,
                        "target_url": target_urls.get(device_code),
                    },
                )
            mode = record.get("mode")
            status = record.get("status")
            current_command_code = record.get("current_command_code")
            if mode != "AUTO" or status != "IDLE" or current_command_code is not None:
                return self._rejected(
                    getattr(device, "work_line_id", None),
                    "START_ADMISSION_DEVICE_NOT_IDLE",
                    f"START 准入失败: 设备 {device_code} 非 AUTO/IDLE 或仍有关联指令",
                    {
                        "device_code": device_code,
                        "mode": mode,
                        "status": status,
                        "current_command_code": current_command_code,
                        "target_url": target_urls.get(device_code),
                    },
                )
        return None

    async def _record_success(
        self,
        db: AsyncSession,
        workline: Any,
        *,
        request_id: str | None,
        trace_id: str | None,
    ) -> None:
        now = timezone.now_for_db()
        _ = await self.workline_status_projection_service.project_ready_after_start(
            db,
            workline_id=workline.id,
            occurred_at=now,
        )
        workline.start_admission_status = _SUCCESS
        workline.start_admission_message = "START 准入通过"
        workline.start_admission_failed_device_code = None
        workline.start_admission_checked_at = now
        workline.last_start_request_id = request_id
        workline.last_start_trace_id = trace_id
        released_outbox_count = await self.outbox_repo.release_blocked_by_workline(db, workline.id)
        await db.commit()
        if released_outbox_count > 0:
            try:
                self._queue_gateway.enqueue_outbox(targets=(OutboxDispatchTarget.SYSTEM,), limit=50)
            except Exception as exc:
                logger.warning(f"START 准入已释放 Outbox，但即时派发触发失败，将依赖 Beat/重试兜底: {exc}")

    async def _record_failure(
        self,
        db: AsyncSession,
        workline: Any,
        *,
        message: str,
        diagnostic: dict[str, Any],
        request_id: str | None,
        trace_id: str | None,
    ) -> None:
        now = timezone.now_for_db()
        workline.start_admission_status = _FAILED
        workline.start_admission_message = message
        workline.start_admission_failed_device_code = self._diagnostic_device_code(diagnostic)
        workline.start_admission_checked_at = now
        workline.last_start_request_id = request_id
        workline.last_start_trace_id = trace_id
        await db.commit()

    @staticmethod
    def _extract_status_records(payload: Any) -> list[dict[str, Any]] | None:
        try:
            record = _UniformDeviceStatusWire.model_validate(payload)
        except ValidationError:
            return None
        return [record.model_dump(mode="python")]

    @staticmethod
    def _first_check_device_code(checks: list[Any]) -> str | None:
        for check in checks:
            context = getattr(check, "context", {})
            if isinstance(context, dict) and isinstance(context.get("device_code"), str):
                return context["device_code"]
        return None

    @staticmethod
    def _diagnostic_device_code(diagnostic: dict[str, Any]) -> str | None:
        value = diagnostic.get("device_code")
        return value if isinstance(value, str) and value else None

    async def _is_ready(self, db: AsyncSession, workline_id: int) -> bool:
        return await self.workline_status_projection_service.is_ready(db, workline_id=workline_id)

    @classmethod
    def _ready_idempotent_result(
        cls,
        workline_id: int,
        *,
        source_device_code: str | None = None,
        checked_devices: list[Any] | None = None,
    ) -> StartAdmissionResult:
        diagnostic: dict[str, Any] = {
            "workline_id": workline_id,
            "runtime_status": _READY.value,
            "idempotent": True,
        }
        if source_device_code is not None:
            diagnostic["source_device_code"] = source_device_code
        if checked_devices is not None:
            diagnostic["checked_devices"] = checked_devices
        return StartAdmissionResult(
            accepted=True,
            http_status=200,
            reason_code=None,
            message="START 准入已完成",
            workline_id=workline_id,
            diagnostic=diagnostic,
        )

    @staticmethod
    def _target_failure_diagnostic(
        target: StartAdmissionStatusTarget,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        diagnostic: dict[str, Any] = {
            "target_url": target.url,
            "device_code": target.device_code,
        }
        if extra:
            diagnostic.update(extra)
        return diagnostic

    async def _runtime_status(self, db: AsyncSession, workline_id: int) -> str | None:
        runtime_snapshot = await self.workline_status_projection_service.runtime_status_snapshot(
            db,
            workline_id=workline_id,
        )
        return runtime_snapshot.runtime_status

    @staticmethod
    def _rejected(
        workline_id: int | None,
        reason_code: str,
        message: str,
        diagnostic: dict[str, Any],
    ) -> StartAdmissionResult:
        return StartAdmissionResult(
            accepted=False,
            http_status=409,
            reason_code=reason_code,
            message=message,
            workline_id=workline_id,
            diagnostic=diagnostic,
        )

    @staticmethod
    def resolve_status_timeout_seconds(runtime_config: dict[str, Any]) -> float:
        value = runtime_config.get("device_status_timeout_seconds", 2.0)
        if not isinstance(value, int | float):
            value = 2.0
        return float(min(max(value, 1.0), 5.0))

    @staticmethod
    def resolve_batch_concurrency(runtime_config: dict[str, Any]) -> int:
        value = runtime_config.get("device_status_batch_concurrency", 4)
        if not isinstance(value, int):
            value = 4
        return min(max(value, 1), 8)

    @staticmethod
    async def _fetch_status(
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(target.url)
        payload = response.json() if 200 <= response.status_code < 300 else None
        return StartAdmissionStatusFetchResult(status_code=response.status_code, payload=payload)


start_admission_service = WorkLineStartAdmissionService()

__all__ = [
    "StartAdmissionResult",
    "StartAdmissionStatusFetchResult",
    "StartAdmissionStatusTarget",
    "WorkLineStartAdmissionService",
    "start_admission_service",
]
