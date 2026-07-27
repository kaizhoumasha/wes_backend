"""真实插件间的 SMT source-pick EFFECT 准入边界。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import (
    SystemCapabilityEffectService,
)
from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
    SystemCapabilityIntentService,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_command.contracts import (
    SmtSourcePickCommandInput,
    SmtSourcePickCommandPayload,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_command.definition import (
    DEFINITION as SMT_SOURCE_PICK_COMMAND_DEFINITION,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_command.handler import (
    SmtSourcePickCommandHandler,
)
from src.app.runtime.system_capabilities.outcomes import ContractViolation
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as ROUGH_SORTER_DEFINITION


class _EffectRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def claim_or_match(self, _db: object, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        raise AssertionError("未声明能力不得进入 claim")


class _Db:
    async def flush(self) -> None:
        raise AssertionError("未声明能力不得进入 flush")


@pytest.mark.asyncio
async def test_real_rough_sorter_cannot_claim_smt_source_pick_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实插件身份不得越权调用另一个插件专属的 SMT source-pick EFFECT。"""

    repository = _EffectRepository()
    handler_calls = 0

    async def forbidden_handler_call(*_args: object, **_kwargs: object) -> object:
        nonlocal handler_calls
        handler_calls += 1
        raise AssertionError("未声明能力不得进入 handler")

    monkeypatch.setattr(SmtSourcePickCommandHandler, "__call__", forbidden_handler_call)
    digest = "e" * 64
    service = SystemCapabilityEffectService(
        intent_service=SystemCapabilityIntentService(
            definitions={
                (
                    SMT_SOURCE_PICK_COMMAND_DEFINITION.capability_key,
                    SMT_SOURCE_PICK_COMMAND_DEFINITION.contract_version,
                ): SMT_SOURCE_PICK_COMMAND_DEFINITION
            },
            plugin_definitions={
                (
                    ROUGH_SORTER_DEFINITION.plugin_key,
                    ROUGH_SORTER_DEFINITION.contract_version,
                ): ROUGH_SORTER_DEFINITION
            },
            plugin_index_digest=digest,
            effect_repository=repository,
        )
    )
    pin = {
        "plugin_key": ROUGH_SORTER_DEFINITION.plugin_key,
        "plugin_binding_id": 9,
        "plugin_binding_version": 1,
        "plugin_index_digest": digest,
    }
    ctx = {
        "db": _Db(),
        "session": SimpleNamespace(
            id=31,
            workline_id=3,
            contract_version=ROUGH_SORTER_DEFINITION.contract_version,
            **pin,
        ),
        "work_item": SimpleNamespace(id=41, **pin),
        "plugin_binding": SimpleNamespace(
            id=9,
            binding_version=1,
            plugin_key=ROUGH_SORTER_DEFINITION.plugin_key,
            contract_version=ROUGH_SORTER_DEFINITION.contract_version,
            generated_index_digest=digest,
            is_enabled=True,
            is_revoked=False,
        ),
        "inbox": SimpleNamespace(correlation_id="corr-cross-plugin", execution_session_id=21),
        "trace_id": "trace-cross-plugin",
    }
    intent = RuntimeIntent.system_capability(
        capability_key=SMT_SOURCE_PICK_COMMAND_DEFINITION.capability_key,
        contract_version=SMT_SOURCE_PICK_COMMAND_DEFINITION.contract_version,
        operation_key="cross-plugin-source-pick",
        dispatch_key="system-capability:material_flow.smt_source_pick_command:cross-plugin-source-pick",
        payload=SmtSourcePickCommandInput(
            target_device_id=7,
            action="SORTING_SOURCE_PICK",
            payload=SmtSourcePickCommandPayload(
                handoff_demand_id=1,
                handoff_source_item_id=2,
                claim_attempt_no=1,
                source_pick_request_event_id="evt-cross-plugin",
            ),
            command_code="SOURCE-PICK-CROSS-PLUGIN",
            result_policy="COMMAND_RESULT",
        ),
        precondition={"expected_available": True},
        fact_version="device:v1",
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 9, "binding_version": 1},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )

    result = await service.apply(ctx, intent)

    assert isinstance(result.outcome, ContractViolation)
    assert result.outcome.error_code == "CAPABILITY_CONTRACT_INVALID"
    assert repository.calls == []
    assert handler_calls == 0
