"""粗分机插件接入共享 conformance suite。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import Success
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionOutput,
)
from src.app.runtime.workline_plugins.dispatcher import (
    PinnedPluginSnapshot,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts, decide
from src.app.runtime.workline_plugins.rough_sorter.inputs import parse_scan_completed
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState
from tests.workline_plugins.conformance import (
    PluginConformanceFixture,
    PluginConformanceSuite,
    assert_system_capability_effect_contract,
)


def _system_capability_intent(capability_key: str, contract_version: str):
    return RuntimeIntent.system_capability(
        capability_key=capability_key,
        contract_version=contract_version,
        operation_key="conformance:rough-sorter:1",
        payload={"fixture": True},
        precondition={"expected": "fixture-v1"},
        fact_version="fixture-v1",
        timeout_seconds=30,
        creator_authority="workline-plugin",
        authorization_policy="plugin-definition",
        binding_snapshot={"binding_id": 1},
        provider_snapshot={"profile": "fixture"},
    )


def _config() -> RoughSorterConfig:
    return RoughSorterConfig.model_validate(
        {
            "device_roles": {
                "input_arm": "ROUGH_SORTER_INPUT_ARM",
                "conveyor": "ROUGH_SORTER_CONVEYOR",
                "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
            },
            "pipeline_input_location": "PIPELINE-IN-01",
            "pipeline_output_location": "PIPELINE-OUT-01",
            "ng_location": "NG-01",
            "warehouse_code": "WH-01",
            "owner_code": "OWNER-01",
            "provider_profile": "wms.2026-07-06.material-flow.sandbox",
        }
    )


def _snapshot() -> PinnedPluginSnapshot:
    config = _config()
    return PinnedPluginSnapshot(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        binding_identity="binding:1:1",
        binding_id=1,
        binding_version=1,
        config_hash=sha256_digest(config.model_dump(mode="json")),
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        profile_identity=config.provider_profile,
    )


def _facts() -> RoughSorterFacts:
    snapshot = _snapshot()
    return RoughSorterFacts(
        business_key="PKG-001",
        hhpn="HH-001",
        lot_code="LOT-001",
        binding_snapshot=RoughSorterBindingSnapshot(
            binding_id=snapshot.binding_id,
            binding_version=snapshot.binding_version,
            profile_identity=snapshot.profile_identity,
            plugin_config_hash=snapshot.config_hash,
            generated_index_digest=snapshot.index_digest,
        ),
    )


def _request(*, route: str, state: RoughSorterState, raw_input: dict[str, object]) -> PluginDispatchRequest:
    return PluginDispatchRequest(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        logical_route=route,
        raw_config=_config().model_dump(mode="json"),
        raw_state=state.model_dump(mode="json"),
        context_state=state.model_dump(mode="json"),
        raw_input=raw_input,
        raw_facts=_facts().model_dump(mode="json"),
        snapshot=_snapshot(),
    )


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def execute(self, capability_key: str, contract_version: str, input_data: object) -> GatewayQueryResult:
        self.calls.append((capability_key, contract_version))
        assert input_data is not None
        return GatewayQueryResult(
            outcome=Success(
                payload=RoughSorterInventoryAdmissionOutput(
                    accepted=True,
                    material_code="HH-001",
                    batch_no="LOT-001",
                    warehouse_code="WH-01",
                    matched_item_count=1,
                    available_quantity=1,
                    source_version="fixture-v1",
                )
            ),
            evidence=SimpleNamespace(reference="timeline:wms:1"),
        )


class TestRoughSorterConformance(PluginConformanceSuite):
    """共享合同由基类收集；本文件只提供粗分机 fixture 与业务断言。"""

    @pytest.fixture
    def plugin_conformance(self) -> PluginConformanceFixture:
        ready = RoughSorterState()
        picking = RoughSorterState(phase="PICK_TO_PIPELINE", current_correlation="CMD-PICK-001")

        async def replay(gateway: _Gateway):
            return await decide(
                parse_scan_completed({"data": {"PkgID": "PKG-001"}}),
                state=ready,
                config=_config(),
                facts=_facts(),
                gateway=gateway,
                replay=True,
            )

        return PluginConformanceFixture(
            definition=DEFINITION,
            dispatcher=WorklinePluginDispatcher(),
            config_payload=_config().model_dump(mode="json"),
            state_payload=ready.model_dump(mode="json"),
            decision_request=_request(
                route="SCAN_COMPLETED",
                state=ready,
                raw_input={"data": {"PkgID": "PKG-001"}},
            ),
            query_request=_request(
                route="PICK_AND_PUT_RESULT",
                state=picking,
                raw_input={
                    "command_code": "CMD-PICK-001",
                    "command_type": "PICK_AND_PUT",
                    "result": "SUCCESS",
                    "data": {
                        "measurement_result": "OK",
                        "reel_diameter": 180,
                        "reel_thickness": 16,
                    },
                },
            ),
            gateway_factory=_Gateway,
            replay=replay,
            system_capability_intents=(
                _system_capability_intent("device.device_command_write", "v1"),
                _system_capability_intent("material_flow.material_unit_write", "v1"),
                _system_capability_intent("runtime.session_hold", "v1"),
            ),
        )

    @pytest.mark.asyncio
    async def test_query_admission_moves_rough_sorter_forward(
        self,
        plugin_conformance: PluginConformanceFixture,
    ) -> None:
        result = await plugin_conformance.dispatcher.dispatch(
            request=plugin_conformance.query_request,
            gateway=_Gateway(),
        )

        assert result.outcome_code == "MOVE_FORWARD_PERSISTED"
        assert result.next_state.phase == "MOVING_FORWARD"


def test_conformance_rejects_undeclared_system_capability() -> None:
    with pytest.raises(AssertionError, match="未在插件 Definition 声明"):
        assert_system_capability_effect_contract(
            definition=DEFINITION,
            intents=(_system_capability_intent("runtime.not_declared", "v1"),),
        )


def test_conformance_rejects_unknown_generated_definition() -> None:
    identity = ("runtime.unknown_generated", "v1")
    definition = replace(DEFINITION, allowed_capabilities=(*DEFINITION.allowed_capabilities, identity))

    with pytest.raises(AssertionError, match="不存在于 generated index"):
        assert_system_capability_effect_contract(
            definition=definition,
            intents=(_system_capability_intent(*identity),),
        )


def test_conformance_rejects_query_capability_used_as_effect() -> None:
    identity = ("wms.rough_sorter_inventory_admission", "v1")

    with pytest.raises(AssertionError, match="必须绑定 EFFECT"):
        assert_system_capability_effect_contract(
            definition=DEFINITION,
            intents=(_system_capability_intent(*identity),),
        )


def test_conformance_does_not_use_source_system_as_final_identity() -> None:
    legacy_intent = RuntimeIntent.external_request(
        dispatch_key="fixture",
        target_code="fixture",
        payload={"fixture": True},
        source_system="runtime.not-declared@v999",
        timeout_seconds=30,
    )

    assert_system_capability_effect_contract(
        definition=DEFINITION,
        intents=(_system_capability_intent("runtime.session_hold", "v1"), legacy_intent),
    )
