"""WMS EFFECT status scanner 的真实 PostgreSQL claim/recovery 合同。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import update

from src.app.effect_ledger_status import SystemOutboxStatus
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.repositories.wms_effect_status_repository import WmsEffectStatusRepository
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.wms_effect_status_service import WmsEffectStatusService
from src.app.runtime.system_capabilities.wms.provider_catalog import freeze_wms_effect_binding
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from src.app.wms_integration.ports.effect_status import (
    build_wms_effect_status_binding,
)
from src.utils.timezone import timezone
from tests.contracts.wms_integration.provider_profile_support import (
    build_hmac_provider_profile_payload,
    build_provider_catalog,
)
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES
from tests.support.runtime_binding import seed_runtime_binding
from tests.workline_runtime.system_capabilities.test_wms_effect_status_service import _settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

E08 = "wms.fulfillment.request_rack_supply@v1"


async def _retire_prior_test_effects(db: AsyncSession) -> None:
    await db.execute(
        update(RuntimeIntentLog)
        .where(RuntimeIntentLog.dispatch_key.like("wms-status-pg:%"))
        .values(
            effect_status=RuntimeIntentStatus.COMPLETED,
            status_check_lease_token=None,
            status_check_lease_until=None,
        )
    )
    await db.commit()


async def _seed_effect(
    db: AsyncSession,
    *,
    suffix: str,
    status_check_after: Any,
    status_check_started_at: Any = None,
) -> str:
    workline, binding = await seed_runtime_binding(db, line_code=f"WMS-STATUS-PG-{suffix}")
    execution_session = ExecutionSession(
        workline_id=workline.id,
        plugin_key=binding.plugin_key,
        manifest_version=binding.contract_version,
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=binding.typed_config_hash,
        plugin_index_digest=binding.generated_index_digest,
        state="RUNNING",
    )
    db.add(execution_session)
    await db.flush()
    correlation = ExecutionCorrelation(
        correlation_id=f"wms-status-pg-correlation:{suffix}",
        execution_session_id=execution_session.id,
        trace_id=f"wms-status-pg-trace:{suffix}",
    )
    db.add(correlation)
    await db.flush()

    operation = WMS_OPERATION_BY_IDENTITY[E08]
    dispatch_key = f"wms-status-pg:{suffix}"
    idempotency_key = f"wms-status-pg-idem:{suffix}"
    request_payload = {**REQUEST_FIXTURES[E08], "dispatch_key": dispatch_key}
    canonical = CanonicalPayload.from_projection(request_payload)
    profile_payload = build_hmac_provider_profile_payload()
    profile_payload["server_url"] = "https://wms.example"
    profile_payload["effect_status_path"] = "/northbound/operations/status"
    catalog = build_provider_catalog(profile_payload)
    frozen_submit = freeze_wms_effect_binding(
        catalog=catalog,
        profile_identity=catalog.profile_identity,
        operation_identity=E08,
        target_code=operation.target_code,
    )
    frozen_status = build_wms_effect_status_binding(
        settings_source=_settings(),
        compiled_profile=catalog.compiled_profile,
    ).as_persisted()
    capability_key, capability_version = E08.rsplit("@", maxsplit=1)
    intent = RuntimeIntentLog(
        execution_session_id=execution_session.id,
        correlation_id=correlation.correlation_id,
        provider_code="WMS",
        operation_kind="system_capability_effect",
        target_domain="wms_integration",
        target_action="request_rack_supply",
        idempotency_key=idempotency_key,
        request_hash=canonical.sha256,
        dispatch_key=dispatch_key,
        capability_key=capability_key,
        capability_contract_version=capability_version,
        operation_identity=E08,
        payload_hash=canonical.sha256,
        effect_status=RuntimeIntentStatus.PROPOSED,
        status_check_started_at=status_check_started_at,
        status_check_after=status_check_after,
        status_binding_snapshot_json=frozen_status["snapshot"],
        status_binding_snapshot_hash=frozen_status["snapshot_hash"],
    )
    outbox = SystemOutbox(
        workline_id=workline.id,
        operation_domain="WMS",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        idempotency_key=idempotency_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code=frozen_submit.target_snapshot.code,
        provider_profile_identity=frozen_submit.provider_profile_identity,
        operation_identity=E08,
        provider_profile_hash=frozen_submit.provider_profile_hash,
        binding_revision=frozen_submit.binding_revision,
        target_snapshot_json=frozen_submit.target_snapshot.as_json(),
        target_snapshot_hash=frozen_submit.target_snapshot_hash,
        auth_scheme=frozen_submit.auth_scheme,
        network_trust_mode=frozen_submit.network_trust_mode,
        credential_reference=frozen_submit.credential_reference,
        payload_json=request_payload,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        status=SystemOutboxStatus.SENT,
    )
    db.add_all([intent, outbox])
    await db.commit()
    return dispatch_key


@pytest.mark.integration
@pytest.mark.asyncio
async def test_overlapping_status_scanners_use_skip_locked_on_postgresql(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = timezone.now_for_db()
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        await _retire_prior_test_effects(seed_db)
        await _seed_effect(
            seed_db,
            suffix=f"{suffix}-1",
            status_check_after=now - timedelta(seconds=5),
            status_check_started_at=now - timedelta(seconds=30),
        )
        await _seed_effect(
            seed_db,
            suffix=f"{suffix}-2",
            status_check_after=now - timedelta(seconds=8),
            status_check_started_at=now - timedelta(seconds=10),
        )

    repository = WmsEffectStatusRepository()
    async with integration_session_factory() as first_db, integration_session_factory() as second_db:
        backlog = await repository.get_due_backlog_snapshot(first_db, now=now)
        assert backlog.backlog_count == 2
        assert backlog.max_overdue_age_ms == 8_000
        assert backlog.max_confirmation_age_ms == 30_000

        first = await repository.claim_due_batch(first_db, now=now, lease_seconds=30, limit=1)
        second = await repository.claim_due_batch(second_db, now=now, lease_seconds=30, limit=1)

        assert len(first) == len(second) == 1
        assert first[0].intent.dispatch_key != second[0].intent.dispatch_key


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_claim_replacement_fences_old_token_on_postgresql(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = timezone.now_for_db()
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        await _retire_prior_test_effects(seed_db)
        dispatch_key = await _seed_effect(seed_db, suffix=suffix, status_check_after=now)

    repository = WmsEffectStatusRepository()
    async with integration_session_factory() as first_db:
        old_claim = await repository.claim_by_dispatch_key(
            first_db,
            dispatch_key=dispatch_key,
            now=now,
            lease_seconds=1,
        )
        assert old_claim is not None
        await first_db.commit()

    async with integration_session_factory() as replacement_db:
        replacement = await repository.claim_by_dispatch_key(
            replacement_db,
            dispatch_key=dispatch_key,
            now=now + timedelta(seconds=2),
            lease_seconds=30,
        )
        assert replacement is not None
        await replacement_db.commit()

    async with integration_session_factory() as verify_db:
        assert replacement.lease_token != old_claim.lease_token
        assert (
            await repository.get_claim_for_update(
                verify_db,
                dispatch_key=dispatch_key,
                lease_token=old_claim.lease_token,
            )
            is None
        )
        assert (
            await repository.get_claim_for_update(
                verify_db,
                dispatch_key=dispatch_key,
                lease_token=replacement.lease_token,
            )
            is not None
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hint_and_scanner_race_queries_wms_at_most_once_on_postgresql(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = timezone.now_for_db()
    suffix = uuid4().hex
    async with integration_session_factory() as seed_db:
        await _retire_prior_test_effects(seed_db)
        dispatch_key = await _seed_effect(
            seed_db,
            suffix=suffix,
            status_check_after=now + timedelta(minutes=5),
        )

    query_count = 0

    repository = WmsEffectStatusRepository()
    queue_gateway = SimpleNamespace(enqueue_wms_effect_status=lambda **_kwargs: None)
    service = WmsEffectStatusService(
        repository=repository,
        settings_source=_settings(),
        now=lambda: now,
        queue_gateway=queue_gateway,
    )
    async with integration_session_factory() as hint_db:
        hint = await service.request_status_check_hint(
            hint_db,
            operation_identity=E08,
            idempotency_key=f"wms-status-pg-idem:{suffix}",
            dispatch_key=dispatch_key,
        )
    assert hint.outcome == "SCHEDULED"

    async def query_wms() -> None:
        nonlocal query_count
        query_count += 1
        await asyncio.sleep(0.05)

    async def scan() -> str:
        async with integration_session_factory() as db:
            claims = await repository.claim_due_batch(
                db,
                now=now,
                lease_seconds=30,
                limit=1,
            )
            await db.commit()
        if not claims:
            return "SKIPPED"
        await query_wms()
        return "QUERIED"

    async def immediate() -> str:
        async with integration_session_factory() as db:
            claim = await repository.claim_by_dispatch_key(
                db,
                dispatch_key=dispatch_key,
                now=now,
                lease_seconds=30,
            )
            await db.commit()
        if claim is None:
            return "SKIPPED"
        await query_wms()
        return "QUERIED"

    scanner_results, immediate_result = await asyncio.gather(scan(), immediate())

    assert query_count == 1
    assert sorted([scanner_results, immediate_result]) == ["QUERIED", "SKIPPED"]
