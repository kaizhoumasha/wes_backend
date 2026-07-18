"""所有 Workline Plugin 共用的参数化一致性测试。

具体插件只提供一个 :class:`PluginConformanceFixture`，不得复制这里的公共
合同断言。这样新增插件时，Definition、QUERY/EFFECT 边界、replay 与容量
限制会自动进入默认回归集。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import pytest
from pydantic import BaseModel, ValidationError

from src.app.runtime.extension_identity import canonical_json
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.system_capabilities.definition import SystemCapabilityMode
from src.app.runtime.system_capabilities.gateway import GatewayLimits, GatewayQueryResult
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX
from src.app.runtime.workline_plugins.attempt_coordinator import (
    AttemptWriteSet,
    PluginWriteSetLimits,
    bound_attempt_write_set,
)
from src.app.runtime.workline_plugins.contracts import MAX_PLUGIN_DECISION_INTENTS, PluginDecision
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition
    from src.app.runtime.workline_plugins.dispatcher import PluginDispatchRequest, WorklinePluginDispatcher


class RecordingGateway(Protocol):
    """Conformance 只观察 capability identity，不接触 provider 实现。"""

    calls: list[tuple[str, str]]

    async def execute(
        self,
        capability_key: str,
        contract_version: str,
        input_data: BaseModel | dict[str, object],
    ) -> GatewayQueryResult: ...


@dataclass(frozen=True, slots=True)
class PluginConformanceFixture:
    definition: WorklinePluginDefinition
    dispatcher: WorklinePluginDispatcher
    config_payload: dict[str, object]
    state_payload: dict[str, object]
    decision_request: PluginDispatchRequest
    query_request: PluginDispatchRequest
    gateway_factory: Callable[[], RecordingGateway]
    replay: Callable[[RecordingGateway], Awaitable[PluginDecision[Any]]]
    system_capability_intents: tuple[RuntimeIntent, ...]


def assert_system_capability_effect_contract(
    *,
    definition: WorklinePluginDefinition,
    intents: tuple[RuntimeIntent, ...],
    capability_index: dict[tuple[str, str], Any] = SYSTEM_CAPABILITY_INDEX,
) -> None:
    """SYSTEM_CAPABILITY 的最终 identity 只取 typed intent 字段。"""

    declared = set(definition.allowed_capabilities)
    for intent in intents:
        if intent.kind is not RuntimeIntentKind.SYSTEM_CAPABILITY:
            continue
        identity = (intent.capability_key, intent.contract_version)
        assert identity in declared, f"SYSTEM_CAPABILITY 未在插件 Definition 声明: {identity}"
        capability = capability_index.get(identity)
        assert capability is not None, f"SYSTEM_CAPABILITY 不存在于 generated index: {identity}"
        assert capability.mode is SystemCapabilityMode.EFFECT, f"SYSTEM_CAPABILITY 必须绑定 EFFECT: {identity}"


class PluginConformanceSuite:
    """继承后由 pytest 收集的唯一共享 Plugin 合同 suite。"""

    @pytest.fixture
    def plugin_conformance(self) -> PluginConformanceFixture:
        raise NotImplementedError

    def test_definition_identity_routes_config_and_state_are_closed(
        self,
        plugin_conformance: PluginConformanceFixture,
    ) -> None:
        fixture = plugin_conformance
        definition = fixture.definition

        assert WORKLINE_PLUGIN_INDEX[(definition.plugin_key, definition.contract_version)] is definition
        assert definition.identity == definition.identity
        assert definition.identity.startswith(f"{definition.plugin_key}@{definition.contract_version}:")
        assert definition.routes
        assert tuple(definition.parsers) == definition.routes
        definition.config_model.model_validate(fixture.config_payload)
        definition.state_model.model_validate(fixture.state_payload)
        with pytest.raises(ValidationError):
            definition.config_model.model_validate({**fixture.config_payload, "conformance_unknown": True})
        with pytest.raises(ValidationError):
            definition.state_model.model_validate({**fixture.state_payload, "conformance_unknown": True})

    def test_declared_capabilities_exist_in_generated_index(
        self,
        plugin_conformance: PluginConformanceFixture,
    ) -> None:
        definition = plugin_conformance.definition

        assert definition.allowed_capabilities
        assert all(identity in SYSTEM_CAPABILITY_INDEX for identity in definition.allowed_capabilities)

    @pytest.mark.asyncio
    async def test_decision_is_closed_and_effects_are_declared(
        self,
        plugin_conformance: PluginConformanceFixture,
    ) -> None:
        fixture = plugin_conformance
        gateway = fixture.gateway_factory()

        result = await fixture.dispatcher.dispatch(request=fixture.decision_request, gateway=gateway)

        assert isinstance(result, PluginDecision)
        assert isinstance(result.next_state, fixture.definition.state_model)
        assert result.outcome_code.strip()
        assert len(result.intents) <= MAX_PLUGIN_DECISION_INTENTS
        assert all(isinstance(intent, RuntimeIntent) for intent in result.intents)
        assert_system_capability_effect_contract(
            definition=fixture.definition,
            intents=fixture.system_capability_intents,
        )

    @pytest.mark.asyncio
    async def test_gateway_executes_query_only_and_effects_stay_as_intents(
        self,
        plugin_conformance: PluginConformanceFixture,
    ) -> None:
        fixture = plugin_conformance
        gateway = fixture.gateway_factory()

        result = await fixture.dispatcher.dispatch(request=fixture.query_request, gateway=gateway)

        assert isinstance(result, PluginDecision)
        assert gateway.calls
        assert all(identity in fixture.definition.allowed_capabilities for identity in gateway.calls)
        assert all(SYSTEM_CAPABILITY_INDEX[identity].mode is SystemCapabilityMode.QUERY for identity in gateway.calls)
        assert all(intent.kind is not RuntimeIntentKind.EXTERNAL_REQUEST for intent in result.intents)

    @pytest.mark.asyncio
    async def test_recorded_replay_never_calls_provider_or_creates_effect(
        self,
        plugin_conformance: PluginConformanceFixture,
    ) -> None:
        gateway = plugin_conformance.gateway_factory()

        decision = await plugin_conformance.replay(gateway)

        assert gateway.calls == []
        assert decision.intents == ()
        assert getattr(decision, "zero_new_effect", True) is True

    @pytest.mark.asyncio
    async def test_intent_state_and_evidence_limits_fail_closed(
        self,
        plugin_conformance: PluginConformanceFixture,
    ) -> None:
        fixture = plugin_conformance
        result = await fixture.dispatcher.dispatch(
            request=fixture.decision_request,
            gateway=fixture.gateway_factory(),
        )
        assert isinstance(result, PluginDecision)
        limits = PluginWriteSetLimits()
        accepted = bound_attempt_write_set(
            AttemptWriteSet(evidence=(), next_state=result.next_state, intents=result.intents),
            limits=limits,
        )
        assert accepted.hold_reason is None

        sample_intent = result.intents[0]
        with pytest.raises(ValidationError):
            PluginDecision(
                intents=(sample_intent,) * (MAX_PLUGIN_DECISION_INTENTS + 1),
                next_state=result.next_state,
                outcome_code="TOO_MANY",
            )
        oversized_state = {"value": "界" * (limits.max_next_state_bytes + 1)}
        state_rejected = bound_attempt_write_set(
            AttemptWriteSet(evidence=(), next_state=oversized_state, intents=()),
            limits=limits,
        )
        assert state_rejected.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
        oversized_evidence = {"value": "界" * (limits.max_write_set_bytes + 1)}
        evidence_rejected = bound_attempt_write_set(
            AttemptWriteSet(evidence=(oversized_evidence,), next_state={}, intents=()),
            limits=limits,
        )
        assert evidence_rejected.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"
        gateway_limits = GatewayLimits()
        assert gateway_limits.max_evidence_bytes < gateway_limits.max_total_evidence_bytes
        assert len(canonical_json(oversized_evidence).encode("utf-8")) > gateway_limits.max_evidence_bytes


__all__ = [
    "PluginConformanceFixture",
    "PluginConformanceSuite",
    "RecordingGateway",
    "assert_system_capability_effect_contract",
]
