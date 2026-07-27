"""粗分机插件接入共享 conformance suite。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterBindingSnapshot,
)
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import Success
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, PluginAttemptContext
from src.app.runtime.workline_plugins.dispatcher import (
    PinnedPluginSnapshot,
    PluginAttemptFactSource,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts, decide
from src.app.runtime.workline_plugins.rough_sorter.inputs import parse_scan_completed
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)
from tests.workline_plugins.conformance import PluginConformanceFixture, PluginConformanceSuite


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
        fact_source=PluginAttemptFactSource(
            snapshot=_snapshot(),
            material_fact={
                "material_identity_key": "PKG-001",
                "six_in_one": {"HHPN": "HH-001", "LotCode": "LOT-001"},
            },
        ),
        snapshot=_snapshot(),
    )


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def execute(self, capability_key: str, contract_version: str, input_data: object) -> GatewayQueryResult:
        self.calls.append((capability_key, contract_version))
        assert isinstance(input_data, InventoryQueryOperationRequest)
        return GatewayQueryResult(
            outcome=Success(
                payload=InventoryQueryOperationResult(
                    items=(
                        InventoryAuthorityItem(
                            material_code=input_data.material_code,
                            available_quantity=1,
                            lot_no=input_data.lot_no,
                            warehouse_code=input_data.warehouse_code,
                            owner_code=input_data.owner_code,
                        ),
                    ),
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

        conversion_context = PluginAttemptContext(
            attempt_id="conformance-attempt",
            inbox_id=1,
            session_id=1,
            workline_id=1,
            event_type="SCAN_COMPLETED",
            payload={},
            plugin_state=ready.model_dump(mode="json"),
            snapshot=AttemptSnapshot(
                processor_token="conformance-lease",
                session_version=1,
                plugin_state_version=0,
                device_fact_versions=(("ROUGH_SORTER_INPUT_ARM", 31, 0),),
                binding_id=1,
                binding_version=1,
            ),
            runtime=SimpleNamespace(),
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
                route="COMMAND_RESULT",
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
            effect_context=conversion_context,
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
