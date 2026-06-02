"""WorkLine START 准入服务。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from src.app.device.repositories import device_repository
from src.app.device.services.device_context_service import device_context_service
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.repositories.runtime_hold_repository import runtime_hold_repository
from src.app.workline.repositories.safety_incident_repository import workline_safety_incident_repository
from src.app.workline.repositories.session_repository import workline_session_repository
from src.app.workline.repositories.workline_repository import workline_repository
from src.app.workline.services.workline_service import WorkLineService, workline_service
from src.core.logger import logger
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone
from src.workline_plugin_registry import get_workline_plugin_definition

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SUCCESS = "SUCCESS"
_FAILED = "FAILED"
_READY = WorkLineRuntimeStatus.READY
_STOPPED = WorkLineRuntimeStatus.STOPPED


@dataclass(frozen=True, slots=True)
class StartAdmissionStatusTarget:
    """单个 ECS status 批量探测目标。"""

    scheme: str
    host: str
    port: int
    status_path: str
    device_codes: tuple[str, ...]

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.status_path}"


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


type StatusFetcher = Callable[[StartAdmissionStatusTarget, float], Awaitable[StartAdmissionStatusFetchResult]]


class WorkLineStartAdmissionService:
    """处理平台 START 事件到 READY 的准入检查。"""

    def __init__(
        self,
        *,
        status_fetcher: StatusFetcher | None = None,
        outbox_repo: SystemOutboxRepository | None = None,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        self.status_fetcher = status_fetcher or self._fetch_status
        self.outbox_repo = outbox_repo or system_outbox_repository
        self._queue_gateway = queue_gateway

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
        if self._is_ready(current):
            return self._ready_idempotent_result(
                workline_id,
                source_device_code=source_device_code,
                checked_devices=[getattr(device, "device_code", None) for device in target_devices],
            )
        final_guard = await self._guard_startable(db, current)
        if final_guard is not None:
            await self._record_failure(
                db,
                current,
                message=final_guard,
                diagnostic={"workline_id": workline_id, "runtime_status": self._runtime_status(current)},
                request_id=request_id,
                trace_id=trace_id,
            )
            return self._rejected(
                workline_id,
                "START_ADMISSION_STATE_CHANGED",
                final_guard,
                {"workline_id": workline_id, "runtime_status": self._runtime_status(current)},
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
        if self._is_ready(workline):
            return self._ready_idempotent_result(workline_id)

        guard_message = await self._guard_startable(db, workline)
        if guard_message is not None:
            await self._record_failure(
                db,
                workline,
                message=guard_message,
                diagnostic={"workline_id": workline_id, "runtime_status": self._runtime_status(workline)},
                request_id=request_id,
                trace_id=trace_id,
            )
            return self._rejected(
                workline_id,
                "START_ADMISSION_NOT_STARTABLE",
                guard_message,
                {"workline_id": workline_id, "runtime_status": self._runtime_status(workline)},
            )

        devices = await device_repository.get_by_work_line_id(db, workline_id)
        checks = workline_service._build_configuration_checks(workline, devices)
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
        runtime_status = self._runtime_status(workline)
        if runtime_status != _STOPPED.value:
            return f"START 准入失败: WorkLine 当前运行态不是 STOPPED: {runtime_status}"
        if getattr(workline, "active_safety_incident_id", None) is not None:
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
        definition = get_workline_plugin_definition(getattr(workline, "plugin_key", None))
        if definition is None:
            return []
        target_map = WorkLineService._command_target_device_map(definition.manifest, devices)
        return [target_map[device_id][0] for device_id in sorted(target_map)]

    def _build_status_targets(self, devices: list[Any]) -> list[StartAdmissionStatusTarget]:
        groups: dict[tuple[str, str, int, str], set[str]] = {}
        for device in devices:
            device_code = getattr(device, "device_code", None)
            host = getattr(device, "host", None)
            port = getattr(device, "port", None)
            status_path = WorkLineService._resolve_device_status_path(device)
            if not isinstance(device_code, str) or not device_code or not isinstance(host, str) or not status_path:
                continue
            if not isinstance(port, int):
                continue
            key = (WorkLineService._resolve_device_scheme(device), host, port, status_path)
            groups.setdefault(key, set()).add(device_code)
        return [
            StartAdmissionStatusTarget(
                scheme=scheme,
                host=host,
                port=port,
                status_path=status_path,
                device_codes=tuple(sorted(device_codes)),
            )
            for (scheme, host, port, status_path), device_codes in sorted(groups.items())
        ]

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
                    if device_code is None:
                        return self._rejected(
                            None,
                            "START_ADMISSION_ECS_BAD_JSON",
                            "START 准入失败: ECS status 响应缺少 device_code",
                            self._target_failure_diagnostic(target, {"record": record}),
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
            for device_code in target.device_codes:
                if device_code not in status_by_device_code:
                    return (
                        self._rejected(
                            None,
                            "START_ADMISSION_DEVICE_STATUS_MISSING",
                            f"START 准入失败: ECS status 未返回设备 {device_code}",
                            self._target_failure_diagnostic(target, {"device_code": device_code}),
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

        if self._is_ready(current):
            return self._ready_idempotent_result(workline_id)

        final_guard = await self._guard_startable(db, current)
        if final_guard is not None:
            diagnostic = {"workline_id": workline_id, "runtime_status": self._runtime_status(current)}
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
        target_urls = {device_code: target.url for target in targets for device_code in target.device_codes}
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
            state = record.get("state")
            state_dict = state if isinstance(state, dict) else {}
            mode = state_dict.get("mode", record.get("mode"))
            status = state_dict.get("status", state_dict.get("device_status", record.get("status")))
            current_command_id = state_dict.get("current_command_id", record.get("current_command_id"))
            if mode != "AUTO" or status != "IDLE" or current_command_id is not None:
                return self._rejected(
                    getattr(device, "work_line_id", None),
                    "START_ADMISSION_DEVICE_NOT_IDLE",
                    f"START 准入失败: 设备 {device_code} 非 AUTO/IDLE 或仍有关联指令",
                    {
                        "device_code": device_code,
                        "mode": mode,
                        "status": status,
                        "current_command_id": current_command_id,
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
        workline.runtime_status = _READY
        workline.stopped_reason = None
        workline.resumed_at = now
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
                self._queue_gateway.enqueue_outbox(limit=50)
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
        records: Any
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(payload.get("devices"), list):
                records = payload["devices"]
            elif isinstance(data, list):
                records = data
            elif isinstance(data, dict) and isinstance(data.get("devices"), list):
                records = data["devices"]
            elif isinstance(payload.get("device_code"), str):
                records = [payload]
            else:
                return None
        else:
            return None
        if not all(isinstance(item, dict) for item in records):
            return None
        return list(records)

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

    @classmethod
    def _is_ready(cls, workline: Any) -> bool:
        return cls._runtime_status(workline) == _READY.value

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
            "device_codes": target.device_codes,
            "device_code": target.device_codes[0] if target.device_codes else None,
        }
        if extra:
            diagnostic.update(extra)
            if "device_code" not in extra and target.device_codes:
                diagnostic["device_code"] = target.device_codes[0]
        return diagnostic

    @staticmethod
    def _runtime_status(workline: Any) -> str | None:
        value = getattr(workline, "runtime_status", None)
        enum_value = getattr(value, "value", value)
        return enum_value if isinstance(enum_value, str) else None

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
