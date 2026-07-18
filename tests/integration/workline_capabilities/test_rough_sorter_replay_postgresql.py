"""粗分机 deterministic replay 与 zero-new-effect PostgreSQL 证据。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func
from sqlmodel import select

from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import SystemCapabilityEffectService
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterBindingSnapshot,
)
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts, decide
from src.app.runtime.workline_plugins.rough_sorter.inputs import BusinessTimeoutInput, ReplayRequestInput
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState
from tests.integration.workline_capabilities.test_system_capability_effect_postgresql import (
    _effect_context,
    _hold_intent,
)
from tests.support.runtime_inbox_processing_postgresql import seed_scan_flow, with_temporary_runtime_database


class _ProviderMustNotRun:
    calls = 0

    async def execute(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        type(self).calls += 1
        raise AssertionError("replay/timeout must not call QUERY provider")


def _config() -> RoughSorterConfig:
    return RoughSorterConfig(
        device_roles={
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        pipeline_input_location="PIPELINE-IN-IT",
        pipeline_output_location="PIPELINE-OUT-IT",
        ng_location="NG-IT",
        warehouse_code="WH-IT",
        owner_code="OWNER-IT",
        provider_profile="wms.v1.sandbox",
    )


def _facts(*, digest_matches: bool | None) -> RoughSorterFacts:
    return RoughSorterFacts(
        business_key="PKG-IT-001",
        hhpn="MAT-IT-001",
        lot_code="LOT-IT-001",
        replay_digest_matches=digest_matches,
        binding_snapshot=RoughSorterBindingSnapshot(
            binding_id=1,
            binding_version=1,
            profile_identity="wms.v1.sandbox",
            plugin_config_hash="a" * 64,
            generated_index_digest="b" * 64,
        ),
    )


def test_same_digest_replay_and_timeout_never_call_query_or_create_success_evidence() -> None:
    """同 digest replay 是纯 no-op；timeout replay 不得伪造 QUERY success evidence。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        _ProviderMustNotRun.calls = 0
        gateway = _ProviderMustNotRun()
        replay = await decide(
            ReplayRequestInput(idempotency_key="replay-1", payload_digest="a" * 64),
            state=RoughSorterState(phase="PICK_TO_PIPELINE", current_correlation="CMD-1"),
            config=_config(),
            facts=_facts(digest_matches=True),
            gateway=gateway,
            replay=True,
        )
        timeout = await decide(
            BusinessTimeoutInput(command_code="CMD-1", wait_type="COMMAND_RESULT"),
            state=RoughSorterState(phase="PICK_TO_PIPELINE", current_correlation="CMD-1"),
            config=_config(),
            facts=_facts(digest_matches=None),
            gateway=gateway,
        )
        assert replay.zero_new_effect is True and replay.intents == ()
        assert timeout.outcome_code == "HOLD" and timeout.intents == ()
        assert _ProviderMustNotRun.calls == 0

        async with session_factory() as db:
            await seed_scan_flow(db)
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0

    asyncio.run(with_temporary_runtime_database(scenario))


def test_different_digest_creates_one_hold_then_replay_creates_zero_new_hold() -> None:
    """不同 digest 首次只落一个 Hold；相同 operation replay 复用成功 evidence。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        decision = await decide(
            ReplayRequestInput(idempotency_key="replay-conflict", payload_digest="c" * 64),
            state=RoughSorterState(),
            config=_config(),
            facts=_facts(digest_matches=False),
            gateway=_ProviderMustNotRun(),
        )
        assert decision.outcome_code == "HOLD" and len(decision.intents) == 1

        service = SystemCapabilityEffectService()
        async with session_factory() as db:
            await seed_scan_flow(db)
            ctx = await _effect_context(db)
            hold = _hold_intent(ctx, operation="replay-digest-conflict", reason="IDEMPOTENCY_CONFLICT")
            first = await service.apply(ctx, hold)
            assert first.idempotent_replay is False
            await db.commit()

        async with session_factory() as db:
            ctx = await _effect_context(db)
            replayed = await service.apply(ctx, hold)
            assert replayed.idempotent_replay is True
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 1
            await db.rollback()

    asyncio.run(with_temporary_runtime_database(scenario))
