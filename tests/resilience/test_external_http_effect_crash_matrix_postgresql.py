"""EXTERNAL_HTTP claim/send/evidence 边界的 PostgreSQL crash matrix。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlmodel import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.models.dispatch_attempt import DispatchAttemptStatus, WorklineDispatchAttempt
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.confirm_inbound_effect_preparation_service import (
    ConfirmInboundEffectPreparationService,
)
from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import WorklineDispatchAttemptService
from src.app.runtime.orchestration.services.notify_package_binding_effect_preparation_service import (
    NotifyPackageBindingEffectPreparationService,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_adapter import (
    NotifyPackageBindingEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.gateway import (
    NotifyPackageBindingDispatchGateway,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_adapter import (
    ConfirmInboundEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.gateway import (
    ConfirmInboundDispatchGateway,
)
from src.app.sys.dispatch_concurrency import (
    DispatchBucketPolicy,
    DispatchPolicyRegistry,
    FairDispatchScheduler,
)
from src.app.sys.external_http_transport import ExternalHttpProtocolResult, ExternalHttpTransportResult
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus, SystemOutboxTargetType
from src.app.sys.repositories import SystemOutboxRepository
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.sys.services.outbox_engine import SystemOutboxEngine
from src.app.wms_integration.ports.confirm_inbound_operation import (
    OPERATION_IDENTITY,
    ConfirmInboundOperationRequest,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    OPERATION_IDENTITY as NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY,
)
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    NotifyPackageBindingOperationRequest,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value
from tests.support.external_http import StaticTestCredentialProvider, frozen_outbox_namespace
from tests.support.runtime_inbox_processing_postgresql import with_temporary_runtime_database

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_CRASH_POINTS = (
    "BEFORE_CLAIM",
    "AFTER_CLAIM_COMMIT",
    "BEFORE_SEND",
    "AFTER_SEND",
    "BEFORE_OUTBOX_EVIDENCE",
    "AFTER_OUTBOX_EVIDENCE",
    "BEFORE_ATTEMPT_EVIDENCE",
    "AFTER_ATTEMPT_EVIDENCE",
    "BEFORE_REDUCER_EVIDENCE",
    "AFTER_REDUCER_EVIDENCE",
    "AFTER_EVIDENCE_COMMIT",
)
_CRASH_POINTS_BEFORE_SEND = frozenset({"AFTER_CLAIM_COMMIT", "BEFORE_SEND"})
_CRASH_POINTS_WITH_DURABLE_ACCEPTANCE = frozenset({"BEFORE_CLAIM", "AFTER_EVIDENCE_COMMIT"})


class _SimulatedWorkerCrash(BaseException):
    """模拟进程在命名边界被强制终止，绕过业务 Exception 恢复。"""


@dataclass(slots=True)
class _CrashAtPoint:
    target: str
    observed: list[str] = field(default_factory=list)

    async def __call__(self, point: object, _outbox: Any | None) -> None:
        point_name = str(point)
        self.observed.append(point_name)
        if point_name == self.target:
            raise _SimulatedWorkerCrash(point_name)


@dataclass(slots=True)
class _RecordingAcceptedSender:
    calls: int = 0

    async def __call__(self, _request: Any) -> ExternalHttpTransportResult:
        self.calls += 1
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )


class _TestCredentialProvider:
    """heavy test 只验证 frozen reference 被读取，不接触真实 secret provider。"""

    def resolve(self, _credential_reference: str) -> bytes:
        return b"confirm-inbound-resilience-test-secret"


@dataclass(frozen=True, slots=True)
class _SeededEffect:
    outbox_id: int
    dispatch_key: str


async def _no_workline_messages(_db: Any, _limit: int) -> dict[str, int]:
    return {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}


def _engine(
    *,
    sender: Callable[[Any], Awaitable[ExternalHttpTransportResult]],
    worker_identity: str,
    fault_hook: Any | None = None,
    credential_provider: Any | None = None,
) -> SystemOutboxEngine:
    repository = SystemOutboxRepository()
    return SystemOutboxEngine(
        outbox_repository=repository,
        external_http_sender=sender,
        credential_provider=credential_provider or StaticTestCredentialProvider(),
        workline_domain_dispatcher=_no_workline_messages,
        dispatch_attempt_service=WorklineDispatchAttemptService(),
        dispatch_scheduler=FairDispatchScheduler(
            repository=repository,
            policy_registry=DispatchPolicyRegistry(
                default_policy=DispatchBucketPolicy(
                    max_concurrency=1,
                    rate_limit=100,
                    rate_window_seconds=60,
                    batch_size=1,
                    retry_budget=3,
                    lease_seconds=30,
                )
            ),
            worker_identity=worker_identity,
        ),
        external_http_fault_hook=fault_hook,
    )


async def _seed_effect(db: AsyncSession, *, suffix: str) -> _SeededEffect:
    dispatch_key = f"effect-resilience-crash:{suffix}"
    execution_session = ExecutionSession(workline_id=991, manifest_version="v1", state="RUNNING")
    db.add(execution_session)
    await db.flush()
    assert execution_session.id is not None
    correlation = ExecutionCorrelation(
        correlation_id=f"effect-resilience-correlation:{suffix}",
        execution_session_id=execution_session.id,
        trace_id=f"effect-resilience-trace:{suffix}",
    )
    db.add(correlation)
    await db.flush()
    intent = RuntimeIntentLog(
        execution_session_id=execution_session.id,
        correlation_id=correlation.correlation_id,
        provider_code="WMS",
        operation_kind="system_capability_effect",
        target_domain="wms_integration",
        target_action="crash_matrix",
        idempotency_key=f"effect-resilience-idempotency:{suffix}",
        request_hash="a" * 64,
        dispatch_key=dispatch_key,
    )
    projection = {"request_id": dispatch_key, "effect": "crash_matrix"}
    frozen = frozen_outbox_namespace(
        projection,
        target_code="WMS_EFFECT_RESILIENCE",
        target_url="https://wms.example/effects/resilience",
        provider_profile_identity="tests.effect-resilience.external-http.v1",
        operation_identity="tests.effect-resilience.effect@v1",
    )
    outbox = SystemOutbox(
        **vars(frozen),
        operation_domain="T8G_RESILIENCE",
        operation_key=f"effect-resilience-operation:{suffix}",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        status=SystemOutboxStatus.NEW,
        trace_id=f"effect-resilience-trace:{suffix}",
    )
    db.add_all([intent, outbox])
    await db.commit()
    assert outbox.id is not None
    return _SeededEffect(outbox_id=int(outbox.id), dispatch_key=dispatch_key)


async def _seed_confirm_inbound_effect(db: AsyncSession, *, suffix: str) -> _SeededEffect:
    """通过 T9 adapter 写入真实 frozen outbox，而不是复制 dispatcher 数据形状。"""

    dispatch_key = f"wms-confirm-inbound:WMS:{suffix}"
    execution_session = ExecutionSession(workline_id=992, manifest_version="v1", state="RUNNING")
    db.add(execution_session)
    await db.flush()
    assert execution_session.id is not None
    correlation = ExecutionCorrelation(
        correlation_id=f"confirm-inbound-resilience-correlation:{suffix}",
        execution_session_id=execution_session.id,
        trace_id=f"confirm-inbound-resilience-trace:{suffix}",
    )
    db.add(correlation)
    await db.flush()
    intent = RuntimeIntentLog(
        execution_session_id=execution_session.id,
        correlation_id=correlation.correlation_id,
        provider_code="RUNTIME",
        operation_kind="system_capability_effect",
        target_domain="wms",
        target_action=suffix,
        idempotency_key=f"confirm-inbound-resilience:{suffix}",
        request_hash="b" * 64,
        dispatch_key=dispatch_key,
        capability_key="wms.inventory.confirm_inbound",
        capability_contract_version="v1",
        operation_identity=suffix,
        completion_mode="OUTBOX_ASYNC",
    )
    request = ConfirmInboundOperationRequest(
        dispatch_key=dispatch_key,
        inbound_key=suffix,
        material_code="MAT-RESILIENCE-001",
        quantity=Decimal("1"),
        warehouse_code="WH-RESILIENCE",
        trace_id=correlation.trace_id,
    )
    adapter = ConfirmInboundEffectAdapter(
        gateway=ConfirmInboundDispatchGateway(
            registry=EndpointRegistry({"WMS_INBOUND_CONFIRM": "https://wms.example/api/wes/inventory/confirm-inbound"})
        )
    )
    outbox = await ConfirmInboundEffectPreparationService().prepare(
        db,
        request=request,
        intent_log=intent,
        adapter=adapter,
    )
    await db.commit()
    assert outbox.id is not None and outbox.operation_identity == OPERATION_IDENTITY
    return _SeededEffect(outbox_id=int(outbox.id), dispatch_key=dispatch_key)


async def _seed_notify_package_binding_effect(db: AsyncSession, *, suffix: str) -> _SeededEffect:
    """通过 T10 adapter 写入真实 frozen outbox，而不是复制 dispatcher 数据形状。"""

    provider_code = "WMS"
    package_id = f"PKG-{suffix}"
    pallet_id = f"PALLET-{suffix}"
    dispatch_key = f"wms-notify-pkg-binding:{provider_code}:{package_id}:{pallet_id}"
    execution_session = ExecutionSession(workline_id=993, manifest_version="v1", state="RUNNING")
    db.add(execution_session)
    await db.flush()
    assert execution_session.id is not None
    correlation = ExecutionCorrelation(
        correlation_id=f"notify-package-binding-resilience-correlation:{suffix}",
        execution_session_id=execution_session.id,
        trace_id=f"notify-package-binding-resilience-trace:{suffix}",
    )
    db.add(correlation)
    await db.flush()
    intent = RuntimeIntentLog(
        execution_session_id=execution_session.id,
        correlation_id=correlation.correlation_id,
        provider_code="RUNTIME",
        operation_kind="system_capability_effect",
        target_domain="wms",
        target_action=suffix,
        idempotency_key=f"notify-package-binding-resilience:{suffix}",
        request_hash="c" * 64,
        dispatch_key=dispatch_key,
        capability_key="wms.fulfillment.notify_pkg_binding",
        capability_contract_version="v1",
        operation_identity=f"{provider_code}:{package_id}:{pallet_id}",
        completion_mode="OUTBOX_ASYNC",
    )
    request = NotifyPackageBindingOperationRequest(
        dispatch_key=dispatch_key,
        provider_code=provider_code,
        package_id=package_id,
        pallet_id=pallet_id,
        station_code="STATION-RESILIENCE",
        trace_id=correlation.trace_id,
    )
    adapter = NotifyPackageBindingEffectAdapter(
        gateway=NotifyPackageBindingDispatchGateway(
            registry=EndpointRegistry(
                {"WMS_PACKAGE_BINDING": "https://wms.example/api/wes/fulfillment/package-binding"}
            )
        )
    )
    outbox = await NotifyPackageBindingEffectPreparationService().prepare(
        db,
        request=request,
        intent_log=intent,
        adapter=adapter,
    )
    await db.commit()
    assert outbox.id is not None
    assert outbox.operation_identity == NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY
    return _SeededEffect(outbox_id=int(outbox.id), dispatch_key=dispatch_key)


async def _expire_claimed_lease(db: AsyncSession, *, outbox_id: int) -> None:
    expired_at = timezone.now_for_db() - timedelta(seconds=1)
    await db.execute(update(SystemOutbox).where(SystemOutbox.id == outbox_id).values(lease_expires_at=expired_at))
    await db.execute(
        update(WorklineDispatchAttempt)
        .where(
            WorklineDispatchAttempt.outbox_id == outbox_id,
            WorklineDispatchAttempt.status == DispatchAttemptStatus.DISPATCHING,
        )
        .values(lease_expires_at=expired_at)
    )
    await db.commit()


async def _load_effect_state(
    db: AsyncSession,
    *,
    seeded: _SeededEffect,
) -> tuple[SystemOutbox, WorklineDispatchAttempt, RuntimeIntentLog, ReconciliationCase | None]:
    outbox = await db.get(SystemOutbox, seeded.outbox_id)
    attempts = list(
        (
            await db.execute(
                select(WorklineDispatchAttempt).where(WorklineDispatchAttempt.dispatch_key == seeded.dispatch_key)
            )
        )
        .scalars()
        .all()
    )
    intent = await db.scalar(select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == seeded.dispatch_key))
    case = await db.scalar(select(ReconciliationCase).where(ReconciliationCase.dispatch_key == seeded.dispatch_key))
    assert outbox is not None and len(attempts) == 1 and intent is not None
    return outbox, attempts[0], intent, case


async def _assert_durable_acceptance(db: AsyncSession, *, seeded: _SeededEffect) -> None:
    outbox, attempt, intent, case = await _load_effect_state(db, seeded=seeded)
    assert enum_value(outbox.status) == SystemOutboxStatus.SENT.value
    assert enum_value(attempt.status) == DispatchAttemptStatus.SENT.value
    assert attempt.transport_outcome == "ACCEPTED"
    assert enum_value(intent.effect_status) == RuntimeIntentStatus.ACCEPTED.value
    assert [item["event_type"] for item in intent.outcome_history_json] == ["TRANSPORT_ACCEPTED"]
    assert case is None


async def _assert_lease_loss_unknown(db: AsyncSession, *, seeded: _SeededEffect) -> None:
    outbox, attempt, intent, case = await _load_effect_state(db, seeded=seeded)
    assert enum_value(outbox.status) == SystemOutboxStatus.UNKNOWN.value
    assert outbox.next_retry_at is None
    assert enum_value(attempt.status) == DispatchAttemptStatus.UNKNOWN.value
    assert attempt.transport_outcome == "AMBIGUOUS"
    assert attempt.safe_to_retry is False
    assert attempt.response_json["lease_loss"] is True
    assert enum_value(intent.effect_status) == RuntimeIntentStatus.RECONCILING.value
    assert [item["event_type"] for item in intent.outcome_history_json] == [
        "TRANSPORT_AMBIGUOUS",
        "RECONCILIATION_OPENED",
    ]
    assert case is not None
    assert enum_value(case.status) == ReconciliationCaseStatus.OPEN.value
    assert case.reason_code == "STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED"


def test_external_http_crash_matrix_recovers_without_blind_resend_on_postgresql() -> None:
    async def scenario(
        session_factory: async_sessionmaker[AsyncSession],
        _queue_gateway: Any,
    ) -> None:
        for point in _CRASH_POINTS:
            suffix = f"{point.lower()}-{uuid4().hex}"
            async with session_factory() as seed_db:
                seeded = await _seed_effect(seed_db, suffix=suffix)

            sender = _RecordingAcceptedSender()
            hook = _CrashAtPoint(point)
            crashed_worker = _engine(
                sender=sender,
                worker_identity=f"effect-resilience-crashed:{suffix}",
                fault_hook=hook,
            )
            with pytest.raises(_SimulatedWorkerCrash, match=point):
                async with session_factory() as crashed_db:
                    await crashed_worker.dispatch(crashed_db, limit=1)

            if point not in _CRASH_POINTS_WITH_DURABLE_ACCEPTANCE:
                async with session_factory() as expire_db:
                    await _expire_claimed_lease(expire_db, outbox_id=seeded.outbox_id)

            restarted_worker = _engine(
                sender=sender,
                worker_identity=f"effect-resilience-restarted:{suffix}",
            )
            async with session_factory() as restarted_db:
                second = await restarted_worker.dispatch(restarted_db, limit=1)
                if point in _CRASH_POINTS_WITH_DURABLE_ACCEPTANCE:
                    await _assert_durable_acceptance(restarted_db, seeded=seeded)
                else:
                    assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
                    await _assert_lease_loss_unknown(restarted_db, seeded=seeded)

            expected_send_count = 0 if point in _CRASH_POINTS_BEFORE_SEND else 1
            assert sender.calls == expected_send_count, point
            assert hook.observed[-1] == point

    asyncio.run(with_temporary_runtime_database(scenario))


def test_confirm_inbound_after_send_crash_enters_unknown_without_blind_resend_on_postgresql() -> None:
    """T9 frozen outbox 发送后崩溃只能进入 UNKNOWN/RECONCILING，禁止盲重发。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession],
        _queue_gateway: Any,
    ) -> None:
        suffix = f"after-send-{uuid4().hex}"
        async with session_factory() as seed_db:
            seeded = await _seed_confirm_inbound_effect(seed_db, suffix=suffix)

        sender = _RecordingAcceptedSender()
        crashed_worker = _engine(
            sender=sender,
            worker_identity=f"confirm-inbound-crashed:{suffix}",
            fault_hook=_CrashAtPoint("AFTER_SEND"),
            credential_provider=_TestCredentialProvider(),
        )
        with pytest.raises(_SimulatedWorkerCrash, match="AFTER_SEND"):
            async with session_factory() as crashed_db:
                await crashed_worker.dispatch(crashed_db, limit=1)

        async with session_factory() as expire_db:
            await _expire_claimed_lease(expire_db, outbox_id=seeded.outbox_id)

        restarted_worker = _engine(
            sender=sender,
            worker_identity=f"confirm-inbound-restarted:{suffix}",
            credential_provider=_TestCredentialProvider(),
        )
        async with session_factory() as restarted_db:
            second = await restarted_worker.dispatch(restarted_db, limit=1)
            assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
            await _assert_lease_loss_unknown(restarted_db, seeded=seeded)
            outbox = await restarted_db.get(SystemOutbox, seeded.outbox_id)
            assert outbox is not None and outbox.operation_identity == OPERATION_IDENTITY

        assert sender.calls == 1

    asyncio.run(with_temporary_runtime_database(scenario))


def test_notify_package_binding_after_send_crash_enters_unknown_without_blind_resend_on_postgresql() -> None:
    """T10 frozen outbox 发送后崩溃只能进入 UNKNOWN/RECONCILING，禁止盲重发。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession],
        _queue_gateway: Any,
    ) -> None:
        suffix = f"notify-after-send-{uuid4().hex}"
        async with session_factory() as seed_db:
            seeded = await _seed_notify_package_binding_effect(seed_db, suffix=suffix)

        sender = _RecordingAcceptedSender()
        crashed_worker = _engine(
            sender=sender,
            worker_identity=f"notify-package-binding-crashed:{suffix}",
            fault_hook=_CrashAtPoint("AFTER_SEND"),
            credential_provider=_TestCredentialProvider(),
        )
        with pytest.raises(_SimulatedWorkerCrash, match="AFTER_SEND"):
            async with session_factory() as crashed_db:
                await crashed_worker.dispatch(crashed_db, limit=1)

        async with session_factory() as expire_db:
            await _expire_claimed_lease(expire_db, outbox_id=seeded.outbox_id)

        restarted_worker = _engine(
            sender=sender,
            worker_identity=f"notify-package-binding-restarted:{suffix}",
            credential_provider=_TestCredentialProvider(),
        )
        async with session_factory() as restarted_db:
            second = await restarted_worker.dispatch(restarted_db, limit=1)
            assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
            await _assert_lease_loss_unknown(restarted_db, seeded=seeded)
            outbox = await restarted_db.get(SystemOutbox, seeded.outbox_id)
            assert outbox is not None
            assert outbox.operation_identity == NOTIFY_PACKAGE_BINDING_OPERATION_IDENTITY

        assert sender.calls == 1

    asyncio.run(with_temporary_runtime_database(scenario))


def test_unexpected_sender_exception_fail_closes_unknown_without_secret_or_resend_on_postgresql() -> None:
    async def scenario(
        session_factory: async_sessionmaker[AsyncSession],
        _queue_gateway: Any,
    ) -> None:
        suffix = uuid4().hex
        async with session_factory() as seed_db:
            seeded = await _seed_effect(seed_db, suffix=suffix)

        calls = 0

        async def unexpected_sender(_request: Any) -> ExternalHttpTransportResult:
            nonlocal calls
            calls += 1
            raise RuntimeError("must-not-leak-secret-value")

        first_worker = _engine(
            sender=unexpected_sender,
            worker_identity=f"effect-resilience-unexpected:first:{suffix}",
        )
        async with session_factory() as first_db:
            first = await first_worker.dispatch(first_db, limit=1)
            assert first == {"dispatched": 1, "success": 0, "failed": 1, "skipped": 0}

        second_worker = _engine(
            sender=unexpected_sender,
            worker_identity=f"effect-resilience-unexpected:second:{suffix}",
        )
        async with session_factory() as second_db:
            second = await second_worker.dispatch(second_db, limit=1)
            assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
            outbox, attempt, intent, case = await _load_effect_state(second_db, seeded=seeded)

        assert calls == 1
        assert enum_value(outbox.status) == SystemOutboxStatus.UNKNOWN.value
        assert enum_value(attempt.status) == DispatchAttemptStatus.UNKNOWN.value
        assert enum_value(intent.effect_status) == RuntimeIntentStatus.RECONCILING.value
        assert case is not None and enum_value(case.status) == ReconciliationCaseStatus.OPEN.value
        persisted_evidence = f"{outbox.last_error} {attempt.error_message} {attempt.response_json}"
        assert "must-not-leak-secret-value" not in persisted_evidence
        assert "RuntimeError" in persisted_evidence

    asyncio.run(with_temporary_runtime_database(scenario))


def test_lease_loss_during_send_fences_old_worker_and_never_sends_twice_on_postgresql() -> None:
    async def scenario(
        session_factory: async_sessionmaker[AsyncSession],
        _queue_gateway: Any,
    ) -> None:
        suffix = uuid4().hex
        async with session_factory() as seed_db:
            seeded = await _seed_effect(seed_db, suffix=suffix)

        calls = 0

        async def accepted_after_lease_loss(_request: Any) -> ExternalHttpTransportResult:
            nonlocal calls
            calls += 1
            async with session_factory() as fence_db:
                await _expire_claimed_lease(fence_db, outbox_id=seeded.outbox_id)
                repository = SystemOutboxRepository()
                fence_scheduler = FairDispatchScheduler(
                    repository=repository,
                    policy_registry=DispatchPolicyRegistry(),
                    worker_identity=f"effect-resilience-lease-fence:{suffix}",
                )
                fenced = await fence_scheduler.claim(
                    fence_db,
                    limit=1,
                    exclude_operation_domains=("WORKLINE", "RACK"),
                )
                await fence_db.commit()
                assert fenced.claims == ()
                assert fenced.metrics.lease_loss_count == 1
            return ExternalHttpTransportResult.accepted(
                http_status_code=202,
                protocol_result=ExternalHttpProtocolResult.ACCEPTED,
            )

        old_worker = _engine(
            sender=accepted_after_lease_loss,
            worker_identity=f"effect-resilience-lease-old:{suffix}",
        )
        async with session_factory() as old_db:
            first = await old_worker.dispatch(old_db, limit=1)
            assert first == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}

        restarted_worker = _engine(
            sender=accepted_after_lease_loss,
            worker_identity=f"effect-resilience-lease-restarted:{suffix}",
        )
        async with session_factory() as restarted_db:
            second = await restarted_worker.dispatch(restarted_db, limit=1)
            assert second == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}
            await _assert_lease_loss_unknown(restarted_db, seeded=seeded)

        assert calls == 1

    asyncio.run(with_temporary_runtime_database(scenario))
