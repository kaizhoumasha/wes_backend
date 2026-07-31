"""WMS EFFECT 状态确认的 orchestration Service。"""

from __future__ import annotations

import asyncio
import math
import random
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.effect_state_contract import (
    EffectReducerEvent,
    EffectReducerEventType,
    generated_effect_source_event_id,
)
from src.app.runtime.orchestration.repositories.wms_effect_status_repository import (
    WmsEffectStatusBacklogSnapshot,
    WmsEffectStatusClaim,
    WmsEffectStatusRepository,
    wms_effect_status_repository,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer, effect_reducer
from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.wms_integration.effect_runtime import (
    interpret_async_effect_ack_response,
    typed_wms_effect_ack_hash,
)
from src.app.wms_integration.operation_contract import WmsDomainProjectionKind
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.effect_status import (
    BATCH_EFFECT_OPERATION_IDENTITIES,
    FrozenWmsEffectStatusBinding,
    WmsBatchEffectStatusRequest,
    WmsEffectStatus,
    WmsEffectStatusRequest,
    WmsEffectStatusSnapshot,
    build_wms_effect_status_binding,
)
from src.app.wms_integration.ports.fulfillment_operations import (
    WmsEffectAck,
    validate_batch_terminal_result,
    validate_effect_ack,
    validate_fulfillment_ack,
)
from src.core.conf import settings
from src.core.logger import logger
from src.core.task_queue_gateway import TaskQueueGateway, task_queue_gateway
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.runtime.orchestration.effect_bridges import EffectReconciliationBridge
    from src.app.sys.external_http_transport import ExternalHttpTransportResult
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile


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
_FULFILLMENT_DOMAIN_OPERATIONS = frozenset(
    {
        "wms.fulfillment.request_rack_supply@v1",
        "wms.fulfillment.request_rack_transport@v1",
        "wms.fulfillment.full_box_exchange@v1",
        "wms.fulfillment.move_bins_to_conveyor_entry@v1",
        "wms.fulfillment.move_bins_from_conveyor_exit@v1",
    }
)
_FULFILLMENT_TERMINAL_NON_SUCCESS = "WMS_FULFILLMENT_TERMINAL_NON_SUCCESS"


def _emit_status_hint_enqueue_failure(
    *,
    operation_identity: str,
    dispatch_key: str,
) -> None:
    """发射 enqueue 降级；观测失败不能反向改变 callback ACK。"""

    from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

    _ = emit_wms_effect_observation(
        "wms_effect.callback_hint",
        operation_identity=operation_identity,
        dispatch_key=dispatch_key,
        attributes={"outcome": "ENQUEUE_DEGRADED"},
    )


def freeze_wms_effect_status_binding(
    *,
    intent_log: Any,
    outbox: Any,
    compiled_profile: CompiledWmsProviderProfile,
) -> None:
    """在 Intent/Outbox 同一短事务加入前冻结非秘密 status binding。"""

    binding = build_wms_effect_status_binding(
        settings_source=settings,
        compiled_profile=compiled_profile,
    )
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
        domain_projector: Any | None = None,
        db_context_factory: Any | None = None,
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
        self._domain_projector = domain_projector
        self._db_context_factory = db_context_factory

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
                    dispatch_key=dispatch_key,
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
            snapshot = await self._query_status(claim=claim, binding=binding, request=request)
        except Exception as exc:
            return await self._record_query_failure(db, claim=claim, error=exc)
        return await self._apply_snapshot(db, claim=claim, snapshot=snapshot)

    async def check_due_batch(self, db: Any) -> tuple[WmsEffectStatusCheckResult, ...]:
        batch_started_at = time.perf_counter()
        scan_now = self._now()
        backlog: WmsEffectStatusBacklogSnapshot | None = None
        try:
            backlog = await self._repository.get_due_backlog_snapshot(db, now=scan_now)
        except Exception:
            with suppress(Exception):
                await db.rollback()
        claims = await self._repository.claim_due_batch(
            db,
            now=scan_now,
            lease_seconds=float(self._settings.WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS),
            limit=int(self._settings.WES_EFFECT_STATUS_SCAN_BATCH_SIZE),
        )
        if not claims:
            self._emit_status_backlog(
                backlog=backlog,
                claimed_count=0,
                duration_ms=(time.perf_counter() - batch_started_at) * 1_000,
            )
            return ()
        await db.commit()

        results: list[WmsEffectStatusCheckResult | None] = [None] * len(claims)
        next_index = iter(enumerate(claims))
        worker_count = min(int(self._settings.WES_EFFECT_STATUS_MAX_IN_FLIGHT), len(claims))

        async def worker() -> None:
            for index, claim in next_index:
                try:
                    async with self._open_db_context() as item_db:
                        try:
                            results[index] = await self._check_claim(item_db, claim=claim)
                        except Exception:
                            with suppress(Exception):
                                await item_db.rollback()
                            raise
                except Exception:
                    logger.exception(
                        f"WMS EFFECT status batch item failed unexpectedly; dispatch_key={claim.intent.dispatch_key}"
                    )
                    # 不清除 lease；当前 worker 失去写回能力时，由 lease 到期后的 scanner 恢复。
                    results[index] = WmsEffectStatusCheckResult(
                        dispatch_key=claim.intent.dispatch_key,
                        outcome="WORKER_FAILED",
                    )

        await asyncio.gather(*(worker() for _ in range(worker_count)))
        completed = tuple(result for result in results if result is not None)
        self._emit_status_backlog(
            backlog=backlog,
            claimed_count=len(claims),
            duration_ms=(time.perf_counter() - batch_started_at) * 1_000,
        )
        return completed

    def _open_db_context(self) -> Any:
        if self._db_context_factory is not None:
            return self._db_context_factory()
        from src.database.db import get_db_context

        return get_db_context()

    async def _check_claim(self, db: Any, *, claim: WmsEffectStatusClaim) -> WmsEffectStatusCheckResult:
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
            snapshot = await self._query_status(claim=claim, binding=binding, request=request)
        except Exception as exc:
            return await self._record_query_failure(db, claim=claim, error=exc)
        return await self._apply_snapshot(db, claim=claim, snapshot=snapshot)

    async def _query_status(
        self,
        *,
        claim: WmsEffectStatusClaim,
        binding: FrozenWmsEffectStatusBinding,
        request: WmsEffectStatusRequest | WmsBatchEffectStatusRequest,
    ) -> WmsEffectStatusSnapshot:
        from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

        started_at = time.perf_counter()
        snapshot = await self._port_factory_builder(binding)().query_status(request)
        _ = emit_wms_effect_observation(
            "wms_effect.status_query",
            operation_identity=claim.outbox.operation_identity,
            dispatch_key=claim.intent.dispatch_key,
            attributes={
                "state": snapshot.state.value,
                "latency_ms": (time.perf_counter() - started_at) * 1_000,
                "retry_count": max(0, int(claim.intent.status_check_count or 0) - 1),
                "age_ms": self._confirmation_age_ms(claim.intent),
            },
        )
        return snapshot

    @staticmethod
    def _emit_status_backlog(
        *,
        backlog: WmsEffectStatusBacklogSnapshot | None,
        claimed_count: int,
        duration_ms: float,
    ) -> None:
        if backlog is None:
            return
        from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

        _ = emit_wms_effect_observation(
            "wms_effect.status_backlog",
            attributes={
                "backlog_count": backlog.backlog_count,
                "max_overdue_age_ms": backlog.max_overdue_age_ms,
                "max_confirmation_age_ms": backlog.max_confirmation_age_ms,
                "claimed_count": claimed_count,
                "duration_ms": duration_ms,
            },
        )

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
        capability_identity = (
            f"{getattr(intent, 'capability_key', '')}@{getattr(intent, 'capability_contract_version', '')}"
        )
        if capability_identity != outbox.operation_identity:
            raise ValueError("WMS status capability identity differs from the frozen EFFECT pair")
        intent_payload_hash = getattr(intent, "payload_hash", None)
        outbox_payload_hash = getattr(outbox, "payload_hash", None)
        if (
            not isinstance(intent_payload_hash, str)
            or not isinstance(outbox_payload_hash, str)
            or intent_payload_hash != outbox_payload_hash
        ):
            raise ValueError("WMS status payload fingerprint differs across the frozen EFFECT pair")
        canonical_payload = CanonicalPayload.from_persisted(
            canonical_payload_bytes=getattr(outbox, "canonical_payload_bytes", None),
            payload_hash=outbox_payload_hash,
        )
        canonical_payload.validate_projection(outbox.payload_json)
        frozen_ack = WmsEffectStatusService._load_frozen_ack(intent)
        request_type = (
            WmsBatchEffectStatusRequest
            if outbox.operation_identity in BATCH_EFFECT_OPERATION_IDENTITIES
            else WmsEffectStatusRequest
        )
        return request_type(
            operation_identity=outbox.operation_identity,
            idempotency_key=intent.idempotency_key,
            attempt_count=max(1, int(intent.status_check_count or 1)),
            request_payload=outbox.payload_json,
            frozen_ack=frozen_ack,
        )

    @staticmethod
    def _load_frozen_ack(intent: Any) -> WmsEffectAck | None:
        """从 append-only reducer evidence 恢复 ACK；尚无 ACK 时允许 status-first。"""

        transport_acceptance_evidence = [
            item
            for item in (getattr(intent, "outcome_history_json", None) or ())
            if isinstance(item, dict) and item.get("event_type") == EffectReducerEventType.TRANSPORT_ACCEPTED.value
        ]
        ack_evidence = [item for item in transport_acceptance_evidence if "typed_ack_hash" in item]
        authoritative = getattr(intent, "outcome_json", None)
        if not transport_acceptance_evidence:
            if isinstance(authoritative, dict):
                typed_outcome = authoritative.get("outcome")
                if isinstance(typed_outcome, dict) and typed_outcome.get("kind") == "success":
                    try:
                        WmsEffectAck.model_validate(typed_outcome.get("payload"))
                    except (TypeError, ValueError) as exc:
                        raise ValueError("persisted WMS EFFECT ACK evidence is invalid") from exc
                    raise ValueError("persisted WMS EFFECT ACK evidence is missing")
            return None
        if len(ack_evidence) != len(transport_acceptance_evidence):
            raise ValueError("persisted WMS EFFECT ACK evidence is invalid")
        try:
            if not isinstance(authoritative, dict):
                raise TypeError("authoritative ACK envelope must be an object")
            typed_outcome = authoritative["outcome"]
            if not isinstance(typed_outcome, dict) or typed_outcome.get("kind") != "success":
                raise ValueError("authoritative ACK outcome must be typed success")
            frozen_ack = WmsEffectAck.model_validate(typed_outcome["payload"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("persisted WMS EFFECT ACK evidence is invalid") from exc
        frozen_payload_hash = getattr(intent, "payload_hash", None)
        if not isinstance(frozen_payload_hash, str) or authoritative.get("payload_hash") != frozen_payload_hash:
            raise ValueError("persisted WMS EFFECT ACK payload fingerprint drifted")
        frozen_ack_hash = typed_wms_effect_ack_hash(frozen_ack)
        expected_reference = f"runtime-intent-outcome:{getattr(intent, 'dispatch_key', '')}"
        if any(
            item.get("typed_ack_hash") != frozen_ack_hash or item.get("typed_ack_reference") != expected_reference
            for item in ack_evidence
        ):
            raise ValueError("persisted WMS EFFECT ACK evidence drifted across submit attempts")
        return frozen_ack

    # 各 fenced/reconciliation 分支必须在自身事务终点显式返回，避免落入普通状态写回。
    async def _apply_snapshot(  # noqa: PLR0911
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
        snapshot_hash = sha256(snapshot.canonical_bytes).hexdigest()
        try:
            request = self._validate_snapshot_matches_claim(current_claim, snapshot)
        except (TypeError, ValueError) as exc:
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code="WMS_STATUS_RESULT_IDENTITY_INVALID",
                evidence={"error_type": type(exc).__name__},
            )
        operation = WMS_OPERATION_BY_IDENTITY[request.operation_identity]
        frozen_ack = request.frozen_ack
        if request.frozen_ack is None and snapshot.recovered_ack is not None:
            recovered_result = await self._record_recovered_ack(
                db,
                claim=current_claim,
                ack=snapshot.recovered_ack,
                source="status",
            )
            if recovered_result is not None:
                return recovered_result
            frozen_ack = snapshot.recovered_ack
        evidence = {
            "snapshot": snapshot.model_dump(mode="json", exclude_none=False),
            "snapshot_hash": snapshot_hash,
            "source_version": snapshot.source_version,
        }
        try:
            current_status = RuntimeIntentStatus(intent.effect_status)
        except (TypeError, ValueError):
            current_status = None
        if current_status in {
            RuntimeIntentStatus.COMPLETED,
            RuntimeIntentStatus.REJECTED,
        } and snapshot.state in {
            WmsEffectStatus.ACCEPTED,
            WmsEffectStatus.PROCESSING,
        }:
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code="WMS_STATUS_TERMINAL_REGRESSION",
                evidence=evidence,
            )
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
        task_outcome = getattr(snapshot.result, "task_outcome", None)
        if snapshot.state is WmsEffectStatus.REJECTED and operation.domain_projection_kind in {
            WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH,
            WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH,
        }:
            if self._domain_projector is None:
                raise RuntimeError("conveyor status reject requires domain projector")
            if await self._domain_projector.should_reconcile_status_reject(
                db,
                operation=operation,
                dispatch_key=intent.dispatch_key,
                request_payload=request.request_payload,
                frozen_ack=frozen_ack,
            ):
                operation_label = (
                    "E13"
                    if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH
                    else "E12"
                )
                evidence_kind = (
                    "ACK_OR_PHYSICAL_EVIDENCE"
                    if operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH
                    else "PHYSICAL_EVIDENCE"
                )
                return await self._open_reconciliation(
                    db,
                    claim=current_claim,
                    reason_code=f"WMS_{operation_label}_REJECT_AFTER_{evidence_kind}",
                    evidence=evidence,
                    frozen_ack=frozen_ack,
                )
        if (
            snapshot.state is WmsEffectStatus.COMPLETED
            and request.operation_identity in _FULFILLMENT_DOMAIN_OPERATIONS
            and task_outcome != "SUCCESS"
        ):
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code=_FULFILLMENT_TERMINAL_NON_SUCCESS,
                evidence={
                    **evidence,
                    "operation_identity": request.operation_identity,
                    "task_outcome": task_outcome,
                },
                frozen_ack=frozen_ack,
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
        reducer_event = EffectReducerEvent(
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
        )
        reduced = await self._reducer.reduce(db, reducer_event)
        if (
            self._domain_projector is not None
            and operation.domain_projection_kind is not None
            and event_type
            in {
                EffectReducerEventType.STATUS_COMPLETED,
                EffectReducerEventType.STATUS_REJECTED,
            }
        ):
            await self._domain_projector.project_event(
                db,
                operation=operation,
                request_payload=request.request_payload,
                event=reducer_event,
                reduction=reduced,
                frozen_ack=frozen_ack,
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
    ) -> WmsEffectStatusRequest:
        request = cls._build_request(claim)
        if (
            snapshot.operation_identity != request.operation_identity
            or snapshot.idempotency_key != request.idempotency_key
        ):
            raise ValueError("WMS status snapshot identity differs from the frozen EFFECT request")
        effective_ack = request.frozen_ack
        if snapshot.recovered_ack is not None:
            validate_effect_ack(
                operation_identity=request.operation_identity,
                idempotency_key=request.idempotency_key,
                ack=snapshot.recovered_ack,
            )
            if effective_ack is not None and typed_wms_effect_ack_hash(
                snapshot.recovered_ack
            ) != typed_wms_effect_ack_hash(effective_ack):
                raise ValueError("recovered WMS status ACK differs from the frozen ACK")
            effective_ack = snapshot.recovered_ack
        if snapshot.state is not WmsEffectStatus.NOT_FOUND and effective_ack is None:
            raise ValueError("visible WMS status requires frozen or recovered ACK")
        if effective_ack is not None and request.operation_identity in BATCH_EFFECT_OPERATION_IDENTITIES:
            operation = WMS_OPERATION_BY_IDENTITY[request.operation_identity]
            batch_request = operation.request_model.model_validate(request.request_payload)
            validate_fulfillment_ack(batch_request, effective_ack)  # type: ignore[arg-type]
        if snapshot.state is not WmsEffectStatus.COMPLETED:
            return request
        result = snapshot.result
        if result is None:
            raise ValueError("WMS completed status is missing typed result")
        expected = request.expected_result_fields
        if any(getattr(result, field_name, None) != expected_value for field_name, expected_value in expected.items()):
            raise ValueError("WMS status result correlation differs from the frozen EFFECT request")
        if effective_ack is not None and request.operation_identity in BATCH_EFFECT_OPERATION_IDENTITIES:
            operation = WMS_OPERATION_BY_IDENTITY[request.operation_identity]
            batch_request = operation.request_model.model_validate(request.request_payload)
            validate_batch_terminal_result(
                batch_request,  # type: ignore[arg-type]
                effective_ack,
                result,  # type: ignore[arg-type]
            )
        return request

    async def _record_recovered_ack(
        self,
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        ack: WmsEffectAck,
        source: str,
    ) -> WmsEffectStatusCheckResult | None:
        """把 status-first/resubmit ACK 写入现有权威 envelope；evidence 只保留 hash/reference。"""

        ack_hash = typed_wms_effect_ack_hash(ack)
        typed_outcome = Success(payload=ack).model_dump(mode="json")
        intent = claim.intent
        evidence = {
            "recovered_typed_ack": True,
            "typed_ack_hash": ack_hash,
            "typed_ack_reference": f"runtime-intent-outcome:{intent.dispatch_key}",
            "outcome_kind": "success",
            "outcome_code": "WMS_ASYNC_ACK_RECOVERED",
        }
        event = EffectReducerEvent(
            event_type=EffectReducerEventType.TRANSPORT_ACCEPTED,
            dispatch_key=intent.dispatch_key,
            occurred_at_ms=self._occurred_at_ms(),
            source_event_id=generated_effect_source_event_id(
                f"wms-{source}-ack",
                intent.dispatch_key,
                ack_hash,
            ),
            attempt_no=max(1, int(getattr(claim.outbox, "attempt_count", 1) or 1)),
            reason_code="WMS_ASYNC_ACK_RECOVERED",
            evidence_json=evidence,
            terminal_outcome=typed_outcome,
        )
        operation = WMS_OPERATION_BY_IDENTITY.get(getattr(claim.outbox, "operation_identity", None))
        request_payload = getattr(claim.outbox, "payload_json", None)
        if (
            self._domain_projector is not None
            and operation is not None
            and operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH
            and isinstance(request_payload, dict)
            and await self._domain_projector.should_reconcile_ack(
                db,
                operation=operation,
                request_payload=request_payload,
                event=event,
            )
        ):
            return await self._open_reconciliation(
                db,
                claim=claim,
                reason_code="WMS_E13_ACK_CONFLICTS_WITH_LOCAL_FACT",
                evidence={
                    **evidence,
                    "trigger_event_type": event.event_type.value,
                    "trigger_source_event_id": event.source_event_id,
                },
            )
        reduced = await self._reducer.reduce(
            db,
            event,
        )
        if (
            self._domain_projector is not None
            and operation is not None
            and operation.domain_projection_kind
            in {
                WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH,
                WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH,
            }
            and isinstance(request_payload, dict)
        ):
            await self._domain_projector.project_event(
                db,
                operation=operation,
                request_payload=request_payload,
                event=event,
                reduction=reduced,
            )
        return None

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
            self._emit_recovery(claim, outcome="NOT_FOUND_GRACE_EXHAUSTED")
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
        self._emit_status_backpressure(
            current_claim,
            reason_code=reason_code if isinstance(reason_code, str) else None,
            retry_after_seconds=retry_after_seconds,
            next_check=next_check,
        )
        return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="RETRY_SCHEDULED")

    def _emit_status_backpressure(
        self,
        claim: WmsEffectStatusClaim,
        *,
        reason_code: str | None,
        retry_after_seconds: object,
        next_check: datetime,
    ) -> None:
        from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

        outcome = (
            "RATE_LIMITED"
            if reason_code == "WMS_RATE_LIMITED"
            else "CIRCUIT_OPEN"
            if reason_code == "WMS_CIRCUIT_OPEN"
            else "TIMEOUT"
            if reason_code == "WMS_PROVIDER_TIMEOUT"
            else "RETRYABLE_FAILURE"
        )
        retry_after_ms = (
            float(retry_after_seconds) * 1_000
            if isinstance(retry_after_seconds, (int, float)) and retry_after_seconds >= 0
            else 0.0
        )
        actual_backoff_ms = max(
            0.0,
            (self._as_db_utc(next_check) - self._as_db_utc(self._now())).total_seconds() * 1_000,
        )
        attributes: dict[str, object] = {
            "outcome": outcome,
            "retry_after_ms": retry_after_ms,
            "actual_backoff_ms": actual_backoff_ms,
        }
        if outcome == "CIRCUIT_OPEN":
            attributes["breaker_state"] = "OPEN"
        _ = emit_wms_effect_observation(
            "wms_effect.status_backpressure",
            operation_identity=claim.outbox.operation_identity,
            dispatch_key=claim.intent.dispatch_key,
            attributes=attributes,
        )

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

    async def _record_resubmit_result(  # noqa: PLR0911
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
        if is_conflict:
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code="WMS_STATUS_RESUBMIT_IDEMPOTENCY_CONFLICT",
                evidence={**evidence, "transport": result.evidence_json()},
            )
        try:
            request = self._build_request(current_claim)
        except (TypeError, ValueError):
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code="WMS_STATUS_RESUBMIT_INDETERMINATE",
                evidence={**evidence, "transport": result.evidence_json()},
            )
        operation = WMS_OPERATION_BY_IDENTITY[request.operation_identity]
        interpreted = interpret_async_effect_ack_response(
            operation,
            request.request_payload,
            idempotency_key=current_claim.intent.idempotency_key,
            payload_hash=current_claim.outbox.payload_hash,
            transport_result=result,
        )
        if isinstance(interpreted, BusinessReject):
            from src.app.runtime.orchestration.effect_bridges import build_wms_async_submit_reject_event

            reject_event = build_wms_async_submit_reject_event(
                dispatch_key=current_claim.intent.dispatch_key,
                attempt_no=max(1, int(current_claim.outbox.attempt_count or 0)),
                occurred_at_ms=self._occurred_at_ms(),
                operation_identity=operation.identity,
                result=result,
                outcome=interpreted,
                additional_evidence=evidence,
            )
            if (
                request.frozen_ack is None
                and self._domain_projector is not None
                and operation.domain_projection_kind is WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH
                and await self._domain_projector.should_reconcile_transport_failure(
                    db,
                    operation=operation,
                    request_payload=request.request_payload,
                )
            ):
                return await self._open_reconciliation(
                    db,
                    claim=current_claim,
                    reason_code=reject_event.reason_code or "WMS_E13_TRANSPORT_FAILURE_AFTER_LOCAL_FACT",
                    evidence={
                        **reject_event.evidence_json,
                        "trigger_event_type": reject_event.event_type.value,
                        "trigger_source_event_id": reject_event.source_event_id,
                    },
                )
            reduced = await self._reducer.reduce(db, reject_event)
            reconciles = request.frozen_ack is not None or bool(getattr(reduced, "contradiction", False))
            if not reconciles and self._domain_projector is not None and operation.domain_projection_kind is not None:
                await self._domain_projector.project_event(
                    db,
                    operation=operation,
                    request_payload=request.request_payload,
                    event=reject_event,
                    reduction=reduced,
                )
            _ = await self._repository.release_claim(
                db,
                claim=current_claim,
                status_check_after=None,
            )
            await db.commit()
            return WmsEffectStatusCheckResult(
                dispatch_key=claim.intent.dispatch_key,
                outcome="RECONCILING" if reconciles else "REJECTED",
                state_changed=bool(getattr(reduced, "state_changed", False)),
            )
        if not isinstance(interpreted, Success):
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code="WMS_STATUS_RESUBMIT_INDETERMINATE",
                evidence={**evidence, "transport": result.evidence_json()},
            )
        frozen_ack = request.frozen_ack
        if frozen_ack is not None and typed_wms_effect_ack_hash(interpreted.payload) != typed_wms_effect_ack_hash(
            frozen_ack
        ):
            return await self._open_reconciliation(
                db,
                claim=current_claim,
                reason_code="WMS_STATUS_RESUBMIT_ACK_DRIFT",
                evidence={**evidence, "transport": result.evidence_json()},
            )
        if frozen_ack is None:
            recovered_result = await self._record_recovered_ack(
                db,
                claim=current_claim,
                ack=interpreted.payload,
                source="resubmit",
            )
            if recovered_result is not None:
                return recovered_result
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
        frozen_ack: WmsEffectAck | None = None,
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
        operation_identity = getattr(claim.outbox, "operation_identity", None)
        operation = WMS_OPERATION_BY_IDENTITY.get(operation_identity)
        if (
            operation is not None
            and operation.domain_projection_kind
            in {
                WmsDomainProjectionKind.FULL_BOX_EXCHANGE_DEMAND,
                WmsDomainProjectionKind.CONVEYOR_INBOUND_BATCH,
                WmsDomainProjectionKind.CONVEYOR_RETURN_BATCH,
            }
            and self._domain_projector is not None
        ):
            await self._domain_projector.project_reconciliation_opened(
                db,
                operation=operation,
                dispatch_key=claim.intent.dispatch_key,
                reason_code=reason_code,
                evidence_json=evidence,
                frozen_ack=frozen_ack,
            )
        _ = await self._repository.release_claim(db, claim=claim, status_check_after=None)
        await db.commit()
        self._emit_recovery(claim, outcome="RECONCILIATION_OPENED")
        if reason_code == _CONFIRMATION_BUDGET_EXHAUSTED:
            self._emit_recovery(claim, outcome="QUERY_BUDGET_EXHAUSTED")
        elif "IDEMPOTENCY_CONFLICT" in reason_code:
            self._emit_recovery(claim, outcome="IDEMPOTENCY_CONFLICT")
        return WmsEffectStatusCheckResult(dispatch_key=claim.intent.dispatch_key, outcome="RECONCILING")

    def _emit_recovery(self, claim: WmsEffectStatusClaim, *, outcome: str) -> None:
        from src.app.runtime.orchestration.wms_effect_observability import emit_wms_effect_observation

        _ = emit_wms_effect_observation(
            "wms_effect.recovery",
            operation_identity=claim.outbox.operation_identity,
            dispatch_key=claim.intent.dispatch_key,
            attributes={
                "outcome": outcome,
                "age_ms": self._confirmation_age_ms(claim.intent),
            },
        )

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

    def _confirmation_age_ms(self, intent: Any) -> float:
        current = self._as_db_utc(self._now())
        started = self._as_db_utc(getattr(intent, "status_check_started_at", None) or current)
        return max(0.0, (current - started).total_seconds() * 1_000)

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


# 组合根延迟导入，避免 services.__init__ 在状态服务定义完成前形成循环依赖。
from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (  # noqa: E402
    wms_fulfillment_domain_projector,
)

wms_effect_status_service = WmsEffectStatusService(
    domain_projector=wms_fulfillment_domain_projector,
)

__all__ = [
    "WmsEffectStatusCheckResult",
    "WmsEffectStatusHintResult",
    "WmsEffectStatusService",
    "freeze_wms_effect_status_binding",
    "wms_effect_status_service",
]
