"""`notify_pkg_binding` typed EFFECT 的 PostgreSQL 双账本与 binding rotation 证据。"""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import TYPE_CHECKING, Any

from sqlalchemy import func
from sqlmodel import select

from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import (
    SystemCapabilityEffectService,
)
from src.app.runtime.system_capabilities.outcomes import Success
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_adapter import (
    NotifyPackageBindingEffectAdapter,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_contract import (
    NotifyPackageBindingEffectAdmission,
    NotifyPackageBindingEffectPrecondition,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.gateway import (
    NotifyPackageBindingDispatchGateway,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.intent_adapter import (
    NotifyPackageBindingIntentAdapter,
)
from src.app.sys.models import SystemOutbox, SystemOutboxStatus
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    OPERATION_IDENTITY,
    NotifyPackageBindingOperationRequest,
)
from src.app.workline.models.plugin_binding import WorklinePluginBinding
from src.app.workline.models.workline import WorkLine
from tests.support.runtime_inbox_processing_postgresql import seed_scan_flow, with_temporary_runtime_database

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _effect_context(db: AsyncSession) -> dict[str, Any]:
    session = await db.scalar(select(WorklineSession))
    inbox = await db.scalar(select(RuntimeInbox))
    work_item = await db.scalar(select(ExecutionWorkItem))
    assert session is not None and inbox is not None and work_item is not None
    binding = await db.get(WorklinePluginBinding, session.plugin_binding_id)
    workline = await db.get(WorkLine, session.workline_id)
    assert binding is not None and workline is not None
    return {
        "db": db,
        "session": session,
        "work_item": work_item,
        "plugin_binding": binding,
        "workline": workline,
        "inbox": inbox,
        "trace_id": inbox.trace_id,
    }


def _intent(
    ctx: dict[str, Any],
    *,
    package_id: str,
    pallet_id: str,
    fact_version: str,
):
    session = ctx["session"]
    provider_code = "WMS"
    request = NotifyPackageBindingOperationRequest(
        dispatch_key=f"wms-notify-pkg-binding:{provider_code}:{package_id}:{pallet_id}",
        provider_code=provider_code,
        package_id=package_id,
        pallet_id=pallet_id,
        station_code="STATION-IT",
        workline_id=session.workline_id,
        session_id=session.id,
        trace_id=ctx["trace_id"],
    )
    admission = NotifyPackageBindingEffectAdmission(
        precondition=NotifyPackageBindingEffectPrecondition(
            package_id=package_id,
            pallet_id=pallet_id,
            local_physical_fact_recorded=True,
        ),
        fact_version=fact_version,
    )
    return NotifyPackageBindingIntentAdapter().build_intent(
        request,
        admission=admission,
        binding_id=session.plugin_binding_id,
        binding_version=session.plugin_binding_version,
    )


def _install_adapter(endpoint_url: str) -> None:
    handler_module = import_module("src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.handler")
    handler_module.notify_package_binding_effect_adapter = NotifyPackageBindingEffectAdapter(
        gateway=NotifyPackageBindingDispatchGateway(registry=EndpointRegistry({"WMS_PACKAGE_BINDING": endpoint_url}))
    )


def test_notify_pkg_binding_replay_is_single_pair_and_rotation_only_affects_new_intent() -> None:
    """同业务 binding identity 零新增；新 binding identity 才冻结轮换后的 endpoint。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        service = SystemCapabilityEffectService()
        first_url = "https://wms-v1.example/api/wes/fulfillment/package-binding"
        second_url = "https://wms-v2.example/api/wes/fulfillment/package-binding"

        async with session_factory() as db:
            await seed_scan_flow(db)
            ctx = await _effect_context(db)
            first_intent = _intent(
                ctx,
                package_id="PKG-IT-001",
                pallet_id="PALLET-IT-001",
                fact_version="runtime-location:v1",
            )
            _install_adapter(first_url)
            first = await service.apply(ctx, first_intent)
            assert isinstance(first.outcome, Success)
            assert first.durably_accepted is True and first.remote_completed is False
            await db.commit()

        async with session_factory() as db:
            ctx = await _effect_context(db)
            replay = await service.apply(ctx, first_intent)
            assert isinstance(replay.outcome, Success)
            assert replay.idempotent_replay is True and replay.durably_accepted is True
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 1
            first_outbox = await db.scalar(select(SystemOutbox))
            assert first_outbox is not None
            assert first_outbox.status is SystemOutboxStatus.NEW
            assert first_outbox.operation_identity == OPERATION_IDENTITY
            assert first_outbox.target_snapshot_json["url"] == first_url
            await db.commit()

        async with session_factory() as db:
            ctx = await _effect_context(db)
            rotated_intent = _intent(
                ctx,
                package_id="PKG-IT-002",
                pallet_id="PALLET-IT-002",
                fact_version="runtime-location:v2",
            )
            _install_adapter(second_url)
            rotated = await service.apply(ctx, rotated_intent)
            assert isinstance(rotated.outcome, Success)
            await db.commit()

        async with session_factory() as db:
            intents = list((await db.execute(select(RuntimeIntentLog).order_by(RuntimeIntentLog.id))).scalars())
            outboxes = list((await db.execute(select(SystemOutbox).order_by(SystemOutbox.id))).scalars())
            assert len(intents) == len(outboxes) == 2
            assert all(intent.effect_status is RuntimeIntentStatus.PROPOSED for intent in intents)
            assert [outbox.dispatch_key for outbox in outboxes] == [intent.dispatch_key for intent in intents]
            assert [outbox.target_snapshot_json["url"] for outbox in outboxes] == [
                first_url,
                second_url,
            ]

    asyncio.run(with_temporary_runtime_database(scenario))
