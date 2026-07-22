"""SystemOutbox 派发引擎。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from socket import gethostname
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import uuid4

from src.app.sys.dispatch_concurrency import FairDispatchScheduler, dispatch_policy_registry
from src.app.sys.external_http_evidence import recover_external_http_evidence_failure_unknown
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpSender,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.models import SystemOutboxDispatchType
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.app.sys.services.endpoint_registry import EndpointRegistry, endpoint_registry
from src.core.logger import logger
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value

if TYPE_CHECKING:
    from src.app.sys.canonical_dispatch import ExternalHttpDispatchRequest

DomainDispatcher = Callable[[Any, int], Awaitable["DispatchResult"]]
WORKLINE_OPERATION_DOMAIN = "WORKLINE"
RACK_OPERATION_DOMAIN = "RACK"
ALLOWED_INTERNAL_SIGNALS = frozenset({"core", "handling", "sys", "workline"})
DEVICE_RESOURCE_WAIT_CODES = frozenset({"DEVICE_BUSY", "DEVICE_STATUS_PRECHECK_WAIT"})


class DispatchResult(TypedDict):
    dispatched: int
    success: int
    failed: int
    skipped: int


class SystemOutboxEngine:
    """统一派发系统级 outbox。

    派发流水线：

        NEW/RETRY_WAIT -> DISPATCHING --send ok--> SENT
                |              |
                |              +--clearly unsent--> RETRY_WAIT / FAILED
                +--blocked by runtime/safety/device--> RETRY_WAIT

    Engine 仅对明确 NOT_SENT 且安全的失败执行有界重试；已经送达或结果不确定时
    不自动重放。下游仍以 dispatch_key/request_id 承担幂等防线。
    """

    MAX_RETRIES = 3

    def __init__(
        self,
        *,
        outbox_repository: SystemOutboxRepository = system_outbox_repository,
        external_http_sender: ExternalHttpSender | None = None,
        endpoint_registry: EndpointRegistry = endpoint_registry,
        workline_domain_dispatcher: DomainDispatcher | None = None,
        device_command_dispatcher: Callable[[Any, Any], Awaitable[bool]] | None = None,
        dispatch_attempt_service: Any | None = None,
        external_http_recovery_context_factory: Callable[[], Any] | None = None,
        effect_transport_bridge: Any | None = None,
        dispatch_scheduler: Any | None = None,
    ) -> None:
        self.outbox_repository = outbox_repository
        self.external_http_sender = external_http_sender or _send_external_http
        self.endpoint_registry = endpoint_registry
        self.workline_domain_dispatcher = workline_domain_dispatcher or _dispatch_workline_domain
        self.device_command_dispatcher = device_command_dispatcher or _dispatch_device_command
        self.dispatch_attempt_service = dispatch_attempt_service
        self.external_http_recovery_context_factory = external_http_recovery_context_factory
        self.effect_transport_bridge = effect_transport_bridge
        self.dispatch_scheduler = dispatch_scheduler or FairDispatchScheduler(
            repository=outbox_repository,
            policy_registry=dispatch_policy_registry,
            worker_identity=f"system-outbox:{gethostname()}:{uuid4().hex}",
        )

    async def dispatch(self, db: Any, limit: int = 50) -> DispatchResult:  # noqa: PLR0912
        result: DispatchResult = {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}

        if limit <= 0:
            return result

        # 为了跨域公平调度，先给 Workline 分配最多一半的额度，避免单一域积压饿死其他域
        workline_limit = max(1, limit // 2)
        workline_result = await self.workline_domain_dispatcher(db, workline_limit)
        _merge_dispatch_result(result, workline_result)

        remaining_limit = max(limit - result["dispatched"], 0)
        if remaining_limit <= 0:
            await _commit_if_supported(db)
            return result

        claim_batch = await self.dispatch_scheduler.claim(
            db,
            limit=remaining_limit,
            exclude_operation_domains=(WORKLINE_OPERATION_DOMAIN, RACK_OPERATION_DOMAIN),
        )
        logger.info(
            "SystemOutbox claim metrics: "
            f"backlog={claim_batch.metrics.backlog_count}, active={claim_batch.metrics.active_lease_count}, "
            f"unknown={claim_batch.metrics.unknown_count}, oldest_age={claim_batch.metrics.oldest_queue_age_seconds}, "
            f"rate_limited={len(claim_batch.metrics.rate_limited_buckets)}, "
            f"paused={len(claim_batch.metrics.paused_buckets)}, "
            f"contended={len(claim_batch.metrics.lease_contended_buckets)}, "
            f"lease_loss={claim_batch.metrics.lease_loss_count}"
        )

        attempt_service = self._resolve_dispatch_attempt_service()
        attempts_by_outbox_id: dict[int, Any] = {}
        for claim in claim_batch.claims:
            outbox_id = getattr(claim.outbox, "id", None)
            if not isinstance(outbox_id, int):
                continue
            attempts_by_outbox_id[outbox_id] = await attempt_service.create_attempt(
                db,
                outbox=claim.outbox,
                auto_commit=False,
            )
        # claim 与整批 attempt 同事务持久化；释放 bucket advisory lock 后，
        # 其它 worker 才能看到完整速率占用。
        await _commit_if_supported(db)

        for claim in claim_batch.claims:
            outbox = claim.outbox
            outbox_id = getattr(outbox, "id", None)
            if not isinstance(outbox_id, int):
                result["skipped"] += 1
                continue

            dispatch_attempt = attempts_by_outbox_id[outbox_id]

            try:
                dispatch_result = await self.dispatch_single(db, outbox)
            except RuntimeError as exc:
                error_code = getattr(exc, "code", None)
                if error_code not in DEVICE_RESOURCE_WAIT_CODES:
                    raise
                _ = await self.outbox_repository.mark_as_blocked_by_device_busy(
                    db,
                    outbox_id,
                    blocked_device_id=getattr(exc, "device_id", None),
                    blocked_workline_id=getattr(outbox, "workline_id", None),
                    reason=error_code,
                    last_error=getattr(exc, "message", str(exc)),
                    detail=dict(getattr(exc, "detail", {}) or {}),
                    lease_owner_token=claim.lease_owner_token,
                )
                _ = await attempt_service.finalize_attempt_record(
                    db,
                    attempt=dispatch_attempt,
                    lease_owner_token=claim.lease_owner_token,
                    success=False,
                    error_message=getattr(exc, "message", str(exc)),
                    response={"result": "blocked", "reason": error_code},
                    auto_commit=False,
                )
                await _commit_if_supported(db)
                result["skipped"] += 1
                result["dispatched"] += 1
                continue
            if isinstance(dispatch_result, ExternalHttpTransportResult):
                try:
                    updated = await self._finalize_external_http_result(
                        db,
                        outbox_id=outbox_id,
                        result=dispatch_result,
                        lease_owner_token=claim.lease_owner_token,
                        retry_budget=claim.policy.retry_budget,
                    )
                    if updated is None:
                        result["skipped"] += 1
                        result["dispatched"] += 1
                        await _commit_if_supported(db)
                        continue
                    outbox_finalization = enum_value(getattr(updated, "status", None)).lower()
                    _ = await attempt_service.finalize_external_http_attempt_record(
                        db,
                        attempt=dispatch_attempt,
                        lease_owner_token=claim.lease_owner_token,
                        result=dispatch_result,
                        outbox_finalization=outbox_finalization,
                        auto_commit=False,
                    )
                    await self._record_effect_transport_result(
                        db,
                        outbox=outbox,
                        dispatch_attempt=dispatch_attempt,
                        result=dispatch_result,
                        updated=updated,
                    )
                    await _commit_if_supported(db)
                except Exception as evidence_error:
                    updated = await recover_external_http_evidence_failure_unknown(
                        db,
                        outbox_repository=self.outbox_repository,
                        outbox_id=outbox_id,
                        lease_owner_token=claim.lease_owner_token,
                        result=dispatch_result,
                        cause=evidence_error,
                        recovery_context_factory=self._resolve_external_http_recovery_context_factory(),
                    )
                    logger.exception(f"SystemOutbox {outbox_id} 证据落库失败，已隔离收口为 UNKNOWN")
                    result["failed"] += 1
                else:
                    if dispatch_result.outcome is ExternalHttpTransportOutcome.ACCEPTED:
                        result["success"] += 1
                    else:
                        result["failed"] += 1
            elif dispatch_result:
                sent = await self.outbox_repository.mark_as_sent(
                    db,
                    outbox_id,
                    lease_owner_token=claim.lease_owner_token,
                )
                if sent is not None:
                    _ = await attempt_service.finalize_attempt_record(
                        db,
                        attempt=dispatch_attempt,
                        lease_owner_token=claim.lease_owner_token,
                        success=True,
                        response={"result": "sent", "outbox_finalization": "sent"},
                        auto_commit=False,
                    )
                await _commit_if_supported(db)
                if sent is None:
                    result["skipped"] += 1
                else:
                    result["success"] += 1
            else:
                failed = await self.outbox_repository.mark_as_failed(
                    db,
                    outbox_id,
                    "Dispatch failed",
                    claim.policy.retry_budget,
                    lease_owner_token=claim.lease_owner_token,
                )
                if failed is not None:
                    _ = await attempt_service.finalize_attempt_record(
                        db,
                        attempt=dispatch_attempt,
                        lease_owner_token=claim.lease_owner_token,
                        success=False,
                        error_message="Dispatch failed",
                        auto_commit=False,
                    )
                await _commit_if_supported(db)
                if failed is None:
                    result["skipped"] += 1
                else:
                    result["failed"] += 1
            result["dispatched"] += 1

        # 如果还有剩余额度，且 Workline 之前已经跑满了分配给它的额度，
        # 则再次派发 Workline 避免吞吐量浪费。
        final_remaining_limit = max(limit - result["dispatched"], 0)
        if final_remaining_limit > 0 and workline_result["dispatched"] >= workline_limit:
            extra_workline_result = await self.workline_domain_dispatcher(db, final_remaining_limit)
            _merge_dispatch_result(result, extra_workline_result)

        await _commit_if_supported(db)
        return result

    def _resolve_dispatch_attempt_service(self) -> Any:
        if self.dispatch_attempt_service is None:
            from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
                workline_dispatch_attempt_service,
            )

            self.dispatch_attempt_service = workline_dispatch_attempt_service
        return self.dispatch_attempt_service

    def _resolve_external_http_recovery_context_factory(self) -> Callable[[], Any]:
        if self.external_http_recovery_context_factory is None:
            from src.database.db import get_db_context

            self.external_http_recovery_context_factory = get_db_context
        return self.external_http_recovery_context_factory

    def _resolve_effect_transport_bridge(self) -> Any:
        if self.effect_transport_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_transport_bridge

            self.effect_transport_bridge = effect_transport_bridge
        return self.effect_transport_bridge

    async def _record_effect_transport_result(
        self,
        db: Any,
        *,
        outbox: Any,
        dispatch_attempt: Any,
        result: ExternalHttpTransportResult,
        updated: Any | None,
    ) -> None:
        if updated is None:
            return
        attempt_no = int(getattr(dispatch_attempt, "attempt_no", None) or 1)
        await self._resolve_effect_transport_bridge().record_result(
            db,
            dispatch_key=str(outbox.dispatch_key),
            attempt_no=attempt_no,
            result=result,
            retry_exhausted=enum_value(getattr(updated, "status", None)) == "FAILED",
            occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
        )

    async def _finalize_external_http_result(
        self,
        db: Any,
        *,
        outbox_id: int,
        result: ExternalHttpTransportResult,
        lease_owner_token: str,
        retry_budget: int,
    ) -> Any | None:
        error = result.error_message or result.error_code or result.outcome.value
        if result.outcome is ExternalHttpTransportOutcome.ACCEPTED:
            return await self.outbox_repository.mark_as_sent(
                db,
                outbox_id,
                lease_owner_token=lease_owner_token,
            )
        if result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS:
            return await self.outbox_repository.mark_as_unknown(
                db,
                outbox_id,
                error,
                lease_owner_token=lease_owner_token,
            )
        if result.safe_to_retry:
            return await self.outbox_repository.mark_as_failed(
                db,
                outbox_id,
                error,
                retry_budget,
                lease_owner_token=lease_owner_token,
            )
        return await self.outbox_repository.mark_as_terminal_failure(
            db,
            outbox_id,
            error,
            lease_owner_token=lease_owner_token,
        )

    async def dispatch_single(self, db: Any, outbox: Any) -> bool | ExternalHttpTransportResult:
        from src.app.sys.services.outbox_delivery import dispatch_external_http, dispatch_internal_signal

        dispatch_type = enum_value(getattr(outbox, "dispatch_type", None))
        if dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP.value:
            return await dispatch_external_http(outbox, self.endpoint_registry, self.external_http_sender)
        if dispatch_type == SystemOutboxDispatchType.INTERNAL_SIGNAL.value:
            return await dispatch_internal_signal(outbox)
        if dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND.value:
            return await self.device_command_dispatcher(db, outbox)
        logger.warning(f"未知的 SystemOutbox 派发类型: {dispatch_type}")
        return False


async def _send_external_http(  # noqa: PLR0911 - 每个 transport 阶段必须显式返回唯一分类
    request: ExternalHttpDispatchRequest,
) -> ExternalHttpTransportResult:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                request.endpoint.url,
                content=request.body,
                headers={
                    "Content-Type": "application/json",
                    "X-WES-Content-SHA256": request.payload_hash,
                },
            )
            status_code = int(response.status_code)
            if 200 <= status_code < 300:
                return ExternalHttpTransportResult.accepted(
                    http_status_code=status_code,
                    protocol_result=ExternalHttpProtocolResult.ACCEPTED,
                )
            if 300 <= status_code < 500:
                return ExternalHttpTransportResult.accepted(
                    http_status_code=status_code,
                    protocol_result=ExternalHttpProtocolResult.REJECTED,
                    error_code="HTTP_REJECTED",
                    error_message=f"HTTP {status_code} explicitly rejected request",
                )
            return ExternalHttpTransportResult.ambiguous(
                phase=ExternalHttpTransportPhase.RESPONSE_RECEIVED,
                protocol_result=ExternalHttpProtocolResult.UNKNOWN,
                http_status_code=status_code,
                error_code="HTTP_RESPONSE_AMBIGUOUS",
                error_message=f"HTTP {status_code} has ambiguous delivery semantics",
            )
    except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
        logger.error(f"SystemOutbox 外部 HTTP 请求配置无效: {exc}")
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.PREPARING,
            safe_to_retry=False,
            error_code=type(exc).__name__.upper(),
            error_message=str(exc),
        )
    except httpx.ConnectError as exc:
        logger.error(f"SystemOutbox 外部 HTTP 连接失败: {exc}")
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.CONNECTING,
            safe_to_retry=True,
            error_code="CONNECT_ERROR",
            error_message=str(exc),
        )
    except httpx.ConnectTimeout as exc:
        logger.error(f"SystemOutbox 外部 HTTP 连接超时，送达状态不确定: {exc}")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.CONNECTING,
            error_code="CONNECT_TIMEOUT",
            error_message=str(exc),
        )
    except (httpx.WriteTimeout, httpx.WriteError) as exc:
        logger.error(f"SystemOutbox 外部 HTTP 写入中断，送达状态不确定: {exc}")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.SENDING,
            error_code=type(exc).__name__.upper(),
            error_message=str(exc),
        )
    except (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
        logger.error(f"SystemOutbox 外部 HTTP 响应中断，送达状态不确定: {exc}")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code=type(exc).__name__.upper(),
            error_message=str(exc),
        )
    except httpx.TimeoutException as exc:
        logger.error(f"SystemOutbox 外部 HTTP 超时，送达状态不确定: {exc}")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.SENDING,
            error_code=type(exc).__name__.upper(),
            error_message=str(exc),
        )
    except Exception as exc:
        logger.error(f"SystemOutbox 外部 HTTP 派发失败: {exc}")
        return ExternalHttpTransportResult.ambiguous(
            phase=ExternalHttpTransportPhase.SENDING,
            error_code="UNCLASSIFIED_TRANSPORT_ERROR",
            error_message=str(exc),
        )


async def _dispatch_workline_domain(db: Any, limit: int) -> DispatchResult:
    from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import outbox_dispatch_service

    return await outbox_dispatch_service.dispatch(db, limit=limit)


async def _dispatch_device_command(db: Any, outbox: Any) -> bool:
    from src.app.runtime.orchestration.services.device_command_gateway import device_command_gateway

    return await device_command_gateway.dispatch(db, outbox)


async def _commit_if_supported(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        result = commit()
        if isawaitable(result):
            await result


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _merge_dispatch_result(target: DispatchResult, source: DispatchResult) -> None:
    for key in ("dispatched", "success", "failed", "skipped"):
        target[key] += int(source.get(key, 0))


system_outbox_engine = SystemOutboxEngine()
system_outbox_dispatcher = system_outbox_engine

__all__ = ["DispatchResult", "SystemOutboxEngine", "system_outbox_dispatcher", "system_outbox_engine"]
