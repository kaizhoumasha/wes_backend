"""WMS EFFECT 状态确认的 orchestration Service。"""

from __future__ import annotations

import math
import random
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    generated_effect_source_event_id,
)
from src.app.runtime.orchestration.repositories.wms_effect_status_repository import (
    WmsEffectStatusClaim,
    WmsEffectStatusRepository,
    wms_effect_status_repository,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer, effect_reducer
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportResult,
)
from src.app.wms_integration.ports.effect_status import (
    CONFIRM_INBOUND_OPERATION_IDENTITY,
    FULL_BOX_EXCHANGE_OPERATION_IDENTITY,
    NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
    ConfirmInboundResultIdentity,
    FrozenWmsEffectStatusBinding,
    FullBoxExchangeResultIdentity,
    NotifyPackageBindingResultIdentity,
    OperationIdentity,
    WmsEffectStatus,
    WmsEffectStatusRequest,
    WmsEffectStatusSnapshot,
    build_wms_effect_status_binding,
)
from src.core.conf import settings
from src.core.logger import logger
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.runtime.orchestration.effect_bridges import EffectReconciliationBridge


@dataclass(frozen=True, slots=True)
class WmsEffectStatusCheckResult:
    dispatch_key: str
    outcome: str
    state_changed: bool = False


@dataclass(frozen=True, slots=True)
class WmsEffectStatusHintResult:
    """callback hint 的持久化调度结果；不表达业务终态。"""

    dispatch_key: str
    outcome: str


_CONFIRMATION_BUDGET_EXHAUSTED = "WMS_STATUS_CONFIRMATION_BUDGET_EXHAUSTED"


def _emit_status_hint_enqueue_failure(
    *,
    operation_identity: str,
    dispatch_key_hash: str,
    error_type: str,
) -> None:
    """发射命名失败指标；观测失败不能反向改变 callback ACK。"""

    from src.app.runtime.orchestration.observability import runtime_observability_registry

    _ = runtime_observability_registry.emit(
        "wms_effect_status_hint.enqueue_failed",
        {
            "operation_identity": operation_identity,
            "dispatch_key_hash": dispatch_key_hash,
            "error_type": error_type,
        },
    )


def freeze_wms_effect_status_binding(*, intent_log: Any, outbox: Any) -> None:
    """在 Intent/Outbox 同一短事务加入前冻结非秘密 status binding。"""

    binding = build_wms_effect_status_binding(settings_source=settings)
    if binding.provider_profile_identity != getattr(outbox, "provider_profile_identity", None):
        raise ValueError("WMS EFFECT status binding must match the frozen outbound provider profile")
    capability_identity = (
        f"{getattr(intent_log, 'capability_key', '')}@{getattr(intent_log, 'capability_contract_version', '')}"
    )
    if capability_identity != outbox.operation_identity:
        raise ValueError("WMS EFFECT intent capability identity differs from the paired outbox")
    persisted = binding.as_persisted()
    intent_log.status_binding_snapshot_json = persisted["snapshot"]
    intent_log.status_binding_snapshot_hash = persisted["snapshot_hash"]


class WmsEffectStatusService:
    """短事务 claim、事务外查询、current-token 写回的唯一编排入口。"""

    def __init__(
        self,
        *,
        repository: WmsEffectStatusRepository = wms_effect_status_repository,
        reducer: EffectReducer = effect_reducer,
        reconciliation_bridge: EffectReconciliationBridge | None = None,
        port_factory_builder: Any | None = None,
        resubmit_dispatcher: Any | None = None,
        settings_source: Any = settings,
        now: Any | None = None,
        jitter: Any | None = None,
        random_source: Any | None = None,
        queue_gateway: TaskQueueGateway = task_queue_gateway,
    ) -> None:
        self._repository = repository
        self._reducer = reducer
        self._reconciliation_bridge = reconciliation_bridge
        self._settings = settings_source
        self._now = now or timezone.now_for_db
        source = random_source or random.SystemRandom()
        self._jitter = jitter or (lambda upper: source.uniform(0.0, upper))
        self._port_factory_builder = port_factory_builder or self._default_port_factory_builder
        self._resubmit_dispatcher = resubmit_dispatcher or self._default_resubmit_dispatcher
        self._queue_gateway = queue_gateway

    async def request_status_check_hint(
        self,
        db: Any,
        *,
        operation_identity: str,
        idempotency_key: str,
        dispatch_key: str,
    ) -> WmsEffectStatusHintResult:
        """持久化提前到期后 best-effort 触发即时查询；Beat 始终是恢复面。"""

        outcome = await self._repository.advance_status_check_after_from_hint(
            db,
            operation_identity=operation_identity,
            idempotency_key=idempotency_key,
            dispatch_key=dispatch_key,
            now=self._now(),
        )
        if outcome in {"NOT_FOUND", "CORRELATION_MISMATCH"}:
            raise ValueError(f"WMS_EFFECT_STATUS_HINT_{outcome}")
        if outcome != "SCHEDULED":
            return WmsEffectStatusHintResult(dispatch_key=dispatch_key, outcome=outcome)

        # 提交是 callback ACK 的持久化边界；之后进程或 broker 故障由 Beat 扫描已到期行恢复。
        await db.commit()
        try:
            self._queue_gateway.enqueue_wms_effect_status(dispatch_key=dispatch_key)
        except Exception as exc:
            dispatch_key_hash = sha256(dispatch_key.encode("utf-8")).hexdigest()[:16]
            error_type = type(exc).__name__
            with suppress(Exception):
                _emit_status_hint_enqueue_failure(
                    operation_identity=operation_identity,
                    dispatch_key_hash=dispatch_key_hash,
                    error_type=error_type,
                )
            logger.warning(
                "metric=wms_effect_status_hint_enqueue_failed_total "
                f"operation_identity={operation_identity} dispatch_key_hash={dispatch_key_hash} "
                f"error_type={error_type}"
            )
        return WmsEffectStatusHintResult(dispatch_key=dispatch_key, outcome=outcome)

    async def check_dispatch(self, db: Any, *, dispatch_key: str) -> WmsEffectStatusCheckResult:
        claim = await self._repository.claim_by_dispatch_key(
            db,
            dispatch_key=dispatch_key,
            now=self._now(),
            lease_seconds=float(self._settings.WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS),
        )
        if claim is None:
            return WmsEffectStatusCheckResult(dispatch_key=dispatch_key, outcome="SKIPPED")
        await db.commit()
        try:
            binding = self._load_binding(claim)
        except (TypeError, ValueError) as exc:
            return await self._record_contract_failure(
                db,
                claim=claim,
                reason_code="WMS_STATUS_BINDING_INVALID",
                error=exc,
            )
        try:
            request = self._build_request(claim)
        except (TypeError, ValueError) as exc:
            return await self._record_contract_failure(
                db,
                claim=claim,
                reason_code="WMS_STATUS_REQUEST_INVALID",
                error=exc,
            )
        try:
            port = self._port_factory_builder(binding)()
            snapshot = await port.query_status(request)
        except Exception as exc:
            return await self._record_query_failure(db, claim=claim, error=exc)
        return await self._apply_snapshot(db, claim=claim, snapshot=snapshot)

    async def check_due_batch(self, db: Any, *, limit: int) -> tuple[WmsEffectStatusCheckResult, ...]:
        results: list[WmsEffectStatusCheckResult] = []
        # 每条记录只在其网络调用即将开始时 claim，避免前项 HTTP 占用后排记录的 lease。
        for _ in range(max(0, limit)):
            claims = await self._repository.claim_due_batch(
                db,
                now=self._now(),
                lease_seconds=float(self._settings.WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS),
                limit=1,
            )
            if not claims:
                break
            claim = claims[0]
            await db.commit()
            try:
                binding = self._load_binding(claim)
            except (TypeError, ValueError) as exc:
                results.append(
                    await self._record_contract_failure(
                        db,
                        claim=claim,
                        reason_code="WMS_STATUS_BINDING_INVALID",
                        error=exc,
                    )
                )
                continue
            try:
                request = self._build_request(claim)
            except (TypeError, ValueError) as exc:
                results.append(
                    await self._record_contract_failure(
                        db,
                        claim=claim,
                        reason_code="WMS_STATUS_REQUEST_INVALID",
                        error=exc,
                    )
                )
                continue
            try:
                snapshot = await self._port_factory_builder(binding)().query_status(request)
            except Exception as exc:
                results.append(await self._record_query_failure(db, claim=claim, error=exc))
            else:
                results.append(await self._apply_snapshot(db, claim=claim, snapshot=snapshot))
        return tuple(results)

    def _default_port_factory_builder(self, binding: FrozenWmsEffectStatusBinding) -> Any:
        # 延迟加载 transport factory，避免 wms_integration adapters
        # 初始化时反向加载 orchestration services。
        from src.app.wms_integration.runtime_factory import build_effect_status_query_port_factory

        return build_effect_status_query_port_factory(
            binding=binding,
            initial_backoff_seconds=float(self._settings.WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS),
            max_backoff_seconds=float(self._settings.WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS),
            settings_source=self._settings,
        )

    @staticmethod
    def _load_binding(claim: WmsEffectStatusClaim) -> FrozenWmsEffectStatusBinding:
        intent = claim.intent
        binding = FrozenWmsEffectStatusBinding.from_persisted(
            snapshot=intent.status_binding_snapshot_json,
            snapshot_hash=str(intent.status_binding_snapshot_hash or ""),
        )
        outbox = claim.outbox
        if (
            binding.provider_profile_identity != outbox.provider_profile_identity
            or binding.provider_profile_hash != outbox.provider_profile_hash
        ):
            raise ValueError("frozen WMS status binding differs from the paired outbox provider")
        return binding

    @staticmethod
    def _build_request(claim: WmsEffectStatusClaim) -> WmsEffectStatusRequest:
        intent = claim.intent
        outbox = claim.outbox
        if (
            intent.dispatch_key != outbox.dispatch_key
            or intent.idempotency_key != outbox.idempotency_key
            or not isinstance(outbox.payload_json, dict)
        ):
            raise ValueError("WMS status query identity differs from the frozen EFFECT pair")
        payload = outbox.payload_json
        identity_by_operation = {
            CONFIRM_INBOUND_OPERATION_IDENTITY: lambda: ConfirmInboundResultIdentity(
                dispatch_key=payload["dispatch_key"],
                inbound_key=payload["inbound_key"],
            ),
            FULL_BOX_EXCHANGE_OPERATION_IDENTITY: lambda: FullBoxExchangeResultIdentity(
                dispatch_key=payload["dispatch_key"],
                rack_id=payload["rack_id"],
                empty_box_id=payload["empty_box_id"],
                full_box_id=payload["full_box_id"],
            ),
            NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY: lambda: NotifyPackageBindingResultIdentity(
                dispatch_key=payload["dispatch_key"],
                package_id=payload["package_id"],
                pallet_id=payload["pallet_id"],
            ),
        }
        identity_factory = identity_by_operation.get(outbox.operation_identity)
        if identity_factory is None:
            raise ValueError("paired outbox is not an authored WMS EFFECT operation")
        try:
            expected_result_identity = identity_factory()
        except KeyError as exc:
            raise ValueError("frozen WMS EFFECT payload is missing a status identity field") from exc
        return WmsEffectStatusRequest(
            operation_identity=cast("OperationIdentity", outbox.operation_identity),
            idempotency_key=intent.idempotency_key,
            attempt_count=max(1, int(intent.status_check_count or 1)),
            expected_result_identity=expected_result_identity,
        )

    async def _apply_snapshot(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        snapshot: WmsEffectStatusSnapshot,
    ) -> WmsEffectStatusCheckResult:
        current_claim = await self._repository.get_claim_for_update(
            db,
            dispatch_key=claim.intent.dispatch_key,
            lease_token=claim.lease_token,
        )
        if current_claim is None:
            await db.rollback()
            return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="FENCED")
        intent = current_claim.intent
        try:
            self._validate_snapshot_matches_claim(current_claim, snapshot)
        except (TypeError, ValueError) as exc:
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code="WMS_STATUS_RESULT_IDENTITY_INVALID",
                evidence={"error_type": type(exc).__name__},
            )
        snapshot_hash = sha256(snapshot.canonical_bytes).hexdigest()
        evidence = {
            "snapshot": snapshot.model_dump(mode="json", exclude_none=False),
            "snapshot_hash": snapshot_hash,
            "source_version": snapshot.source_version,
        }
        version_conflict = self._source_version_conflict(intent, snapshot=snapshot, snapshot_hash=snapshot_hash)
        if version_conflict == "WMS_STATUS_SOURCE_VERSION_REGRESSED":
            return await self._record_stale_snapshot(
                db,
                claim=current_claim,
                evidence=evidence,
            )
        if version_conflict is not None:
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code=version_conflict,
                evidence=evidence,
            )
        event_type = {
            WmsEffectStatus.ACCEPTED: EffectReducerEventType.STATUS_ACCEPTED,
            WmsEffectStatus.PROCESSING: EffectReducerEventType.STATUS_PROCESSING,
            WmsEffectStatus.COMPLETED: EffectReducerEventType.STATUS_COMPLETED,
            WmsEffectStatus.REJECTED: EffectReducerEventType.STATUS_REJECTED,
        }.get(snapshot.state)
        if event_type is None:
            return await self._record_not_found(db, claim=current_claim, evidence=evidence)
        occurred_at_ms = self._occurred_at_ms()
        reduced = await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=event_type,
                dispatch_key=intent.dispatch_key,
                occurred_at_ms=occurred_at_ms,
                source_event_id=generated_effect_source_event_id(
                    "wms-status",
                    intent.dispatch_key,
                    snapshot.source_version,
                    snapshot_hash,
                ),
                reason_code=snapshot.reason_code,
                evidence_json=evidence,
            ),
        )
        contradictory_terminal = self._is_contradictory_terminal(intent, snapshot=snapshot)
        if not contradictory_terminal:
            intent.status_source_version = snapshot.source_version
        if snapshot.state in {WmsEffectStatus.COMPLETED, WmsEffectStatus.REJECTED}:
            next_check = None
        else:
            next_check = self._next_check_within_confirmation_budget(intent)
            if next_check is None:
                return await self._open_confirmation_budget_reconciliation(
                    db,
                    claim=current_claim,
                    evidence=evidence,
                )
        _ = await self._repository.release_claim(db, claim=current_claim, status_check_after=next_check)
        await db.commit()
        return WmsEffectStatusCheckResult(
            dispatch_key=intent.dispatch_key,
            outcome=snapshot.state.value,
            state_changed=bool(getattr(reduced, "state_changed", False)),
        )

    @staticmethod
    def _source_version_conflict(
        intent: Any,
        *,
        snapshot: WmsEffectStatusSnapshot,
        snapshot_hash: str,
    ) -> str | None:
        previous_version = intent.status_source_version
        if previous_version is None or snapshot.source_version is None:
            return None
        if WmsEffectStatusService._is_contradictory_terminal(intent, snapshot=snapshot):
            return None
        if snapshot.source_version < previous_version:
            return "WMS_STATUS_SOURCE_VERSION_REGRESSED"
        if snapshot.source_version > previous_version:
            return None
        previous = next(
            (
                item
                for item in reversed(intent.outcome_history_json or [])
                if item.get("source_version") == previous_version and item.get("snapshot_hash")
            ),
            None,
        )
        if previous is not None and previous.get("snapshot_hash") != snapshot_hash:
            return "WMS_STATUS_SOURCE_VERSION_CONFLICT"
        return None

    @staticmethod
    def _is_contradictory_terminal(intent: Any, *, snapshot: WmsEffectStatusSnapshot) -> bool:
        try:
            current = RuntimeIntentStatus(intent.effect_status)
        except (TypeError, ValueError):
            return False
        return (current, snapshot.state) in {
            (RuntimeIntentStatus.COMPLETED, WmsEffectStatus.REJECTED),
            (RuntimeIntentStatus.REJECTED, WmsEffectStatus.COMPLETED),
        }

    @classmethod
    def _validate_snapshot_matches_claim(
        cls,
        claim: WmsEffectStatusClaim,
        snapshot: WmsEffectStatusSnapshot,
    ) -> None:
        request = cls._build_request(claim)
        if (
            snapshot.operation_identity != request.operation_identity
            or snapshot.idempotency_key != request.idempotency_key
        ):
            raise ValueError("WMS status snapshot identity differs from the frozen EFFECT request")
        if snapshot.state is not WmsEffectStatus.COMPLETED:
            return
        result = snapshot.result
        if result is None:
            raise ValueError("WMS completed status is missing typed result")
        expected = request.expected_result_identity.model_dump(mode="python")
        if any(getattr(result, field_name, None) != expected_value for field_name, expected_value in expected.items()):
            raise ValueError("WMS status result correlation differs from the frozen EFFECT request")

    async def _record_not_found(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        evidence: dict[str, Any],
    ) -> WmsEffectStatusCheckResult:
        intent = claim.intent
        _ = await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=EffectReducerEventType.STATUS_NOT_FOUND,
                dispatch_key=intent.dispatch_key,
                occurred_at_ms=self._occurred_at_ms(),
                source_event_id=generated_effect_source_event_id(
                    "wms-status-not-found",
                    intent.dispatch_key,
                    intent.status_check_count,
                    evidence,
                ),
                evidence_json=evidence,
            ),
        )
        if intent.status_source_version is not None:
            return await self._open_reconciliation(
                db,
                claim=claim,
                reason_code="WMS_STATUS_VISIBILITY_REGRESSED",
                evidence=evidence,
            )
        next_check = self._next_check_within_confirmation_budget(intent)
        if next_check is None:
            return await self._open_confirmation_budget_reconciliation(
                db,
                claim=claim,
                evidence=evidence,
            )
        started_at = intent.status_check_started_at or self._now()
        current = self._as_db_utc(self._now())
        grace = timedelta(seconds=float(self._settings.WES_EFFECT_NOT_FOUND_GRACE_SECONDS))
        if current - self._as_db_utc(started_at) >= grace:
            if int(intent.status_resubmit_count or 0) != 0:
                return await self._open_reconciliation(
                    db,
                    claim=claim,
                    reason_code="WMS_STATUS_NOT_FOUND_AFTER_RESUBMIT",
                    evidence=evidence,
                )
            reserved = await self._repository.reserve_resubmit(db, claim=claim)
            if not reserved:
                return await self._open_reconciliation(
                    db,
                    claim=claim,
                    reason_code="WMS_STATUS_RESUBMIT_ALREADY_RESERVED",
                    evidence=evidence,
                )
            # 崩溃时宁可留下 count=1，也不能在下一次 delivery 再发第二次。
            await db.commit()
            try:
                result = await self._resubmit_dispatcher(claim)
            except Exception as exc:
                current_claim = await self._repository.get_claim_for_update(
                    db,
                    dispatch_key=intent.dispatch_key,
                    lease_token=claim.lease_token,
                )
                if current_claim is None:
                    await db.rollback()
                    return WmsEffectStatusCheckResult(dispatch_key=intent.dispatch_key, outcome="FENCED")
                return await self._open_reconciliation(
                    db,
                    claim=current_claim,
                    reason_code="WMS_STATUS_RESUBMIT_INDETERMINATE",
                    evidence={**evidence, "error_type": type(exc).__name__},
                )
            return await self._record_resubmit_result(db, claim=claim, result=result, evidence=evidence)
        _ = await self._repository.release_claim(db, claim=claim, status_check_after=next_check)
        await db.commit()
        return WmsEffectStatusCheckResult(dispatch_key=intent.dispatch_key, outcome=WmsEffectStatus.NOT_FOUND.value)

    async def _record_query_failure(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        error: Exception,
    ) -> WmsEffectStatusCheckResult:
        current_claim = await self._repository.get_claim_for_update(
            db,
            dispatch_key=claim.intent.dispatch_key,
            lease_token=claim.lease_token,
        )
        if current_claim is None:
            await db.rollback()
            return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="FENCED")
        evidence = {"error_type": type(error).__name__}
        failure = getattr(error, "failure", None)
        reason_code = getattr(failure, "reason_code", None)
        retry_after_seconds = getattr(failure, "retry_after_seconds", None)
        _ = await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=EffectReducerEventType.STATUS_QUERY_FAILED,
                dispatch_key=current_claim.intent.dispatch_key,
                occurred_at_ms=self._occurred_at_ms(),
                source_event_id=generated_effect_source_event_id(
                    "wms-status-query-failed",
                    current_claim.intent.dispatch_key,
                    current_claim.intent.status_check_count,
                    reason_code,
                ),
                reason_code=reason_code if isinstance(reason_code, str) else None,
                evidence_json=evidence,
            ),
        )
        if failure is not None and getattr(failure, "retryable", False) is not True:
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code=(reason_code if isinstance(reason_code, str) else "WMS_STATUS_QUERY_CONTRACT_FAILED"),
                evidence=evidence,
            )
        next_check = self._next_check_within_confirmation_budget(
            current_claim.intent,
            minimum_delay_seconds=(
                float(retry_after_seconds)
                if isinstance(retry_after_seconds, (int, float)) and retry_after_seconds >= 0
                else None
            ),
        )
        if next_check is None:
            return await self._open_confirmation_budget_reconciliation(
                db,
                claim=current_claim,
                evidence=evidence,
            )
        _ = await self._repository.release_claim(
            db,
            claim=current_claim,
            status_check_after=next_check,
        )
        await db.commit()
        return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="RETRY_SCHEDULED")

    async def _record_contract_failure(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        reason_code: str,
        error: Exception,
    ) -> WmsEffectStatusCheckResult:
        current_claim = await self._repository.get_claim_for_update(
            db,
            dispatch_key=claim.intent.dispatch_key,
            lease_token=claim.lease_token,
        )
        if current_claim is None:
            await db.rollback()
            return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="FENCED")
        return await self._open_reconciliation(
            db,
            claim=current_claim,
            reason_code=reason_code,
            evidence={"error_type": type(error).__name__},
        )

    async def _record_stale_snapshot(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        evidence: dict[str, Any],
    ) -> WmsEffectStatusCheckResult:
        _ = await self._reducer.reduce(
            db,
            EffectReducerEvent(
                event_type=EffectReducerEventType.STATUS_STALE,
                dispatch_key=claim.intent.dispatch_key,
                occurred_at_ms=self._occurred_at_ms(),
                source_event_id=generated_effect_source_event_id(
                    "wms-status-stale",
                    claim.intent.dispatch_key,
                    evidence,
                ),
                evidence_json=evidence,
            ),
        )
        next_check = self._next_check_within_confirmation_budget(claim.intent)
        if next_check is None:
            return await self._open_confirmation_budget_reconciliation(
                db,
                claim=claim,
                evidence=evidence,
            )
        _ = await self._repository.release_claim(
            db,
            claim=claim,
            status_check_after=next_check,
        )
        await db.commit()
        return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="STALE")

    async def _record_resubmit_result(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        result: ExternalHttpTransportResult,
        evidence: dict[str, Any],
    ) -> WmsEffectStatusCheckResult:
        current_claim = await self._repository.get_claim_for_update(
            db,
            dispatch_key=claim.intent.dispatch_key,
            lease_token=claim.lease_token,
        )
        if current_claim is None:
            await db.rollback()
            return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="FENCED")
        is_conflict = result.http_status_code == 422 and result.protocol_error_code == "IDEMPOTENCY_CONFLICT"
        is_accepted = result.outcome is ExternalHttpTransportOutcome.ACCEPTED and (
            result.protocol_result is ExternalHttpProtocolResult.ACCEPTED
            or (result.http_status_code == 409 and result.protocol_error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS")
        )
        if is_conflict or not is_accepted:
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code=(
                    "WMS_STATUS_RESUBMIT_IDEMPOTENCY_CONFLICT" if is_conflict else "WMS_STATUS_RESUBMIT_INDETERMINATE"
                ),
                evidence={**evidence, "transport": result.evidence_json()},
            )
        next_check = self._next_check_within_confirmation_budget(current_claim.intent)
        if next_check is None:
            return await self._open_confirmation_budget_reconciliation(
                db,
                claim=current_claim,
                evidence={**evidence, "transport": result.evidence_json()},
            )
        _ = await self._repository.release_claim(
            db,
            claim=current_claim,
            status_check_after=next_check,
        )
        await db.commit()
        return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="RESUBMITTED")

    async def _open_reconciliation(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        reason_code: str,
        evidence: dict[str, Any],
    ) -> WmsEffectStatusCheckResult:
        _ = await self._resolve_reconciliation_bridge().open(
            db,
            dispatch_key=claim.intent.dispatch_key,
            occurred_at_ms=self._occurred_at_ms(),
            reason_code=reason_code,
            evidence_json=evidence,
            source_event_id=generated_effect_source_event_id(
                "wms-status-reconciliation",
                claim.intent.dispatch_key,
                reason_code,
                evidence,
            ),
        )
        _ = await self._repository.release_claim(db, claim=claim, status_check_after=None)
        await db.commit()
        return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="RECONCILING")

    async def _open_confirmation_budget_reconciliation(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        evidence: dict[str, Any],
    ) -> WmsEffectStatusCheckResult:
        return await self._open_reconciliation(
            db,
            claim=claim,
            reason_code=_CONFIRMATION_BUDGET_EXHAUSTED,
            evidence={
                **evidence,
                "confirmation_budget": self._confirmation_budget_evidence(claim.intent),
            },
        )

    def _resolve_reconciliation_bridge(self) -> Any:
        if self._reconciliation_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_reconciliation_bridge

            self._reconciliation_bridge = effect_reconciliation_bridge
        return self._reconciliation_bridge

    def _next_check(
        self,
        intent: Any,
        *,
        minimum_delay_seconds: float | None = None,
    ) -> datetime:
        initial = float(self._settings.WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS)
        maximum = float(self._settings.WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS)
        exponent = max(0, int(intent.status_check_count or 1) - 1)
        saturation = math.ceil(math.log2(maximum / initial)) if maximum > initial else 0
        base = maximum if exponent >= saturation else min(maximum, initial * (2**exponent))
        jitter_window = base / 2
        jitter = max(0.0, min(jitter_window, float(self._jitter(jitter_window))))
        delay = base - jitter
        if minimum_delay_seconds is not None:
            delay = max(delay, minimum_delay_seconds)
        return self._now() + timedelta(seconds=delay)

    def _next_check_within_confirmation_budget(
        self,
        intent: Any,
        *,
        minimum_delay_seconds: float | None = None,
    ) -> datetime | None:
        if int(intent.status_check_count or 0) >= int(self._settings.WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS):
            return None
        current = self._as_db_utc(self._now())
        deadline = self._confirmation_deadline(intent, current=current)
        if current >= deadline:
            return None
        if minimum_delay_seconds is not None and current + timedelta(seconds=minimum_delay_seconds) > deadline:
            return None
        candidate = self._as_db_utc(
            self._next_check(
                intent,
                minimum_delay_seconds=minimum_delay_seconds,
            )
        )
        return min(candidate, deadline)

    def _confirmation_deadline(self, intent: Any, *, current: datetime) -> datetime:
        started_at = getattr(intent, "status_check_started_at", None)
        started = current if started_at is None else self._as_db_utc(started_at)
        return started + timedelta(seconds=float(self._settings.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS))

    def _confirmation_budget_evidence(self, intent: Any) -> dict[str, Any]:
        current = self._as_db_utc(self._now())
        deadline = self._confirmation_deadline(intent, current=current)
        return {
            "status_check_count": int(intent.status_check_count or 0),
            "max_query_attempts": int(self._settings.WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS),
            "status_check_started_at": self._as_db_utc(
                getattr(intent, "status_check_started_at", None) or current
            ).isoformat(),
            "max_confirmation_age_seconds": float(self._settings.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS),
            "deadline": deadline.isoformat(),
        }

    @staticmethod
    def _as_db_utc(value: datetime) -> datetime:
        normalized = timezone.to_db_datetime(value)
        if normalized is None:
            raise TypeError("WMS status confirmation time must be a datetime")
        return normalized

    def _occurred_at_ms(self) -> int:
        current = self._now()
        aware = current if current.tzinfo is not None else current.replace(tzinfo=UTC)
        return int(aware.timestamp() * 1000)

    @staticmethod
    async def _default_resubmit_dispatcher(claim: WmsEffectStatusClaim) -> ExternalHttpTransportResult:
        """复用冻结 Outbox envelope 发送同键新签名，并只追加 attempt ledger。"""

        from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
            workline_dispatch_attempt_service,
        )
        from src.app.sys.services.outbox_delivery import dispatch_external_http
        from src.app.sys.services.outbox_engine import system_outbox_engine
        from src.database.db import get_db_context

        result = await dispatch_external_http(
            claim.outbox,
            system_outbox_engine.credential_provider,
            system_outbox_engine.external_http_sender,
        )
        async with get_db_context() as evidence_db:
            _ = await workline_dispatch_attempt_service.append_status_resubmit_result(
                evidence_db,
                outbox=claim.outbox,
                result=result,
            )
        return result


wms_effect_status_service = WmsEffectStatusService()

__all__ = [
    "WmsEffectStatusCheckResult",
    "WmsEffectStatusHintResult",
    "WmsEffectStatusService",
    "freeze_wms_effect_status_binding",
    "wms_effect_status_service",
]
