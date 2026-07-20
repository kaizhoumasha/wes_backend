"""粗分机 WMS 库存准入 QUERY capability 行为合同。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.app.runtime.system_capabilities import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionInput,
    RoughSorterInventoryAdmissionOutput,
)
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.handler import (
    RoughSorterInventoryAdmissionHandler,
)
from src.app.wms_integration.ports.inventory_query import WmsInventoryItem


def admission_input(**overrides: object) -> RoughSorterInventoryAdmissionInput:
    values: dict[str, object] = {
        "business_key": "scan:RS-01:evt-001",
        "hhpn": "MAT-001",
        "lot_code": "LOT-2026-07",
        "warehouse_code": "WH-A",
        "owner_code": "OWNER-A",
        "diameter_mm": Decimal("12.50"),
        "thickness_mm": Decimal("1.20"),
        "binding_snapshot": RoughSorterBindingSnapshot(
            binding_id=9,
            binding_version=2,
            profile_identity="wms.2026-07-06.material-flow.sandbox",
            plugin_config_hash="a" * 64,
            generated_index_digest="b" * 64,
        ),
    }
    values.update(overrides)
    return RoughSorterInventoryAdmissionInput(**values)


class FakeInventoryPort:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, str | None]] = []

    async def query_inventory(
        self,
        material_code: str,
        *,
        warehouse_code: str | None = None,
    ) -> list[WmsInventoryItem]:
        self.calls.append((material_code, warehouse_code))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_matching_material_and_batch_returns_typed_success() -> None:
    port = FakeInventoryPort(
        [
            WmsInventoryItem(
                material_code="MAT-001",
                warehouse_code="WH-A",
                storage_location_code="BIN-01",
                quantity=Decimal("8.5"),
                batch_no="LOT-2026-07",
            ),
            WmsInventoryItem(
                material_code="MAT-001",
                warehouse_code="WH-A",
                storage_location_code="BIN-02",
                quantity=Decimal("3.5"),
                batch_no="OTHER",
            ),
        ]
    )

    outcome = await RoughSorterInventoryAdmissionHandler(port)(admission_input())

    assert isinstance(outcome, Success)
    assert outcome.payload == RoughSorterInventoryAdmissionOutput(
        accepted=True,
        material_code="MAT-001",
        batch_no="LOT-2026-07",
        warehouse_code="WH-A",
        matched_item_count=1,
        available_quantity=Decimal("8.5"),
        source_version="2026-07-06.material-flow",
    )
    assert port.calls == [("MAT-001", "WH-A")]


@pytest.mark.asyncio
async def test_production_profile_from_binding_is_admitted() -> None:
    port = FakeInventoryPort(
        [
            WmsInventoryItem(
                material_code="MAT-001",
                warehouse_code="WH-A",
                storage_location_code="BIN-01",
                quantity=Decimal("1"),
                batch_no="LOT-2026-07",
            )
        ]
    )
    snapshot = admission_input().binding_snapshot.model_copy(
        update={"profile_identity": "wms.2026-07-06.material-flow.production"}
    )

    outcome = await RoughSorterInventoryAdmissionHandler(port)(admission_input(binding_snapshot=snapshot))

    assert isinstance(outcome, Success)
    assert port.calls == [("MAT-001", "WH-A")]


@pytest.mark.asyncio
async def test_measurements_and_owner_are_validated_but_not_added_to_port_call() -> None:
    port = FakeInventoryPort([])
    request = admission_input(owner_code="OWNER-PRIVATE", diameter_mm="14.2", thickness_mm="0.8")

    outcome = await RoughSorterInventoryAdmissionHandler(port)(request)

    assert isinstance(outcome, BusinessReject)
    assert outcome.reason_code == "WMS_REJECTED"
    assert port.calls == [("MAT-001", "WH-A")]


@pytest.mark.parametrize("field", ["diameter_mm", "thickness_mm"])
def test_measurements_must_be_positive(field: str) -> None:
    with pytest.raises(ValueError):
        admission_input(**{field: 0})


@pytest.mark.parametrize("field", ["business_key", "hhpn", "lot_code", "warehouse_code", "owner_code"])
def test_stable_input_strings_reject_whitespace_only(field: str) -> None:
    with pytest.raises(ValueError):
        admission_input(**{field: "   \t"})


@pytest.mark.parametrize("field", ["profile_identity", "plugin_config_hash", "generated_index_digest"])
def test_binding_snapshot_stable_strings_reject_whitespace_only(field: str) -> None:
    snapshot = admission_input().binding_snapshot.model_dump()
    snapshot[field] = "   \t"

    with pytest.raises(ValueError):
        admission_input(binding_snapshot=snapshot)


@pytest.mark.asyncio
async def test_stable_strings_strip_outer_whitespace_without_casefolding() -> None:
    snapshot = admission_input().binding_snapshot.model_dump()
    snapshot.update(
        {
            "profile_identity": "  wms.2026-07-06.material-flow.sandbox  ",
            "plugin_config_hash": f"  {'a' * 64}  ",
            "generated_index_digest": f"  {'b' * 64}  ",
        }
    )
    port = FakeInventoryPort(
        [
            WmsInventoryItem(
                material_code="Mat-Exact",
                warehouse_code="WH-A",
                storage_location_code="BIN-01",
                quantity=1,
                batch_no="Lot-Exact",
            )
        ]
    )
    request = admission_input(
        business_key="  scan:RS-01:evt-001  ",
        hhpn="  Mat-Exact  ",
        lot_code="  Lot-Exact  ",
        warehouse_code="  WH-A  ",
        owner_code="  OWNER-A  ",
        binding_snapshot=snapshot,
    )

    outcome = await RoughSorterInventoryAdmissionHandler(port)(request)

    assert isinstance(outcome, Success)
    assert request.business_key == "scan:RS-01:evt-001"
    assert request.hhpn == "Mat-Exact"
    assert request.lot_code == "Lot-Exact"
    assert request.owner_code == "OWNER-A"
    assert request.binding_snapshot.plugin_config_hash == "a" * 64
    assert port.calls == [("Mat-Exact", "WH-A")]


@pytest.mark.parametrize(
    ("material_code", "batch_no"),
    [("mat-exact", "Lot-Exact"), ("Mat-Exact", "lot-exact")],
)
@pytest.mark.asyncio
async def test_material_and_batch_matching_remains_case_sensitive(material_code: str, batch_no: str) -> None:
    port = FakeInventoryPort(
        [
            WmsInventoryItem(
                material_code=material_code,
                warehouse_code="WH-A",
                storage_location_code="BIN-01",
                quantity=1,
                batch_no=batch_no,
            )
        ]
    )

    outcome = await RoughSorterInventoryAdmissionHandler(port)(admission_input(hhpn="Mat-Exact", lot_code="Lot-Exact"))

    assert isinstance(outcome, BusinessReject)
    assert outcome.reason_code == "WMS_REJECTED"


@pytest.mark.asyncio
async def test_timeout_or_unavailable_is_closed_retryable_failure() -> None:
    from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryUnavailable

    for error in (TimeoutError(), WmsInventoryQueryUnavailable("WMS unavailable")):
        outcome = await RoughSorterInventoryAdmissionHandler(FakeInventoryPort(error))(admission_input())
        assert isinstance(outcome, RetryableFailure)
        assert outcome.error_code == "WMS_TIMEOUT"


@pytest.mark.asyncio
async def test_stable_port_rejection_is_closed_business_reject() -> None:
    from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryRejected

    outcome = await RoughSorterInventoryAdmissionHandler(
        FakeInventoryPort(WmsInventoryQueryRejected("WMS inventory query rejected"))
    )(admission_input())

    assert isinstance(outcome, BusinessReject)
    assert outcome.reason_code == "WMS_REJECTED"
    assert outcome.message == "WMS inventory query rejected"


@pytest.mark.asyncio
async def test_invalid_provider_shape_is_contract_violation() -> None:
    outcome = await RoughSorterInventoryAdmissionHandler(FakeInventoryPort([{"unexpected": True}]))(admission_input())

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_CONTRACT_INVALID"


@pytest.mark.asyncio
async def test_binding_profile_identity_must_match_capability_definition() -> None:
    mismatched = admission_input(
        binding_snapshot={
            **admission_input().binding_snapshot.model_dump(),
            "profile_identity": "wms.other.sandbox",
        }
    )

    outcome = await RoughSorterInventoryAdmissionHandler(FakeInventoryPort([]))(mismatched)

    assert isinstance(outcome, ContractViolation)
    assert outcome.error_code == "WMS_PROFILE_MISMATCH"


@pytest.mark.asyncio
async def test_gateway_evidence_hash_includes_measurements_and_binding_snapshot() -> None:
    from src.app.runtime.capability_port_registry import CapabilityPortRegistry, RuntimeCapabilityContext
    from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway
    from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.definition import DEFINITION
    from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    result = [
        WmsInventoryItem(
            material_code="MAT-001",
            warehouse_code="WH-A",
            storage_location_code="BIN-01",
            quantity=1,
            batch_no="LOT-2026-07",
        )
    ]
    registry = CapabilityPortRegistry()
    registry.register(WmsInventoryQueryPort, lambda: FakeInventoryPort(result))
    profile = workline_plugin_binding_service.profile_catalog.resolve(
        provider_code="WMS",
        contract_version="2026-07-06.material-flow",
        environment="sandbox",
    )
    gateway = SystemCapabilityGateway(
        attempt_id="attempt-measurement-hash",
        definitions={(DEFINITION.capability_key, DEFINITION.contract_version): DEFINITION},
        allowed_capabilities=frozenset({(DEFINITION.capability_key, DEFINITION.contract_version)}),
        context=RuntimeCapabilityContext.from_provider_profile(registry, profile),
        admission_profile=profile.identity,
    )

    first = await gateway.execute(DEFINITION.capability_key, DEFINITION.contract_version, admission_input())
    second = await gateway.execute(
        DEFINITION.capability_key,
        DEFINITION.contract_version,
        admission_input(diameter_mm="12.51"),
    )

    assert isinstance(first.outcome, Success)
    assert isinstance(second.outcome, Success)
    assert first.evidence is not None
    assert second.evidence is not None
    assert first.evidence.input_hash != second.evidence.input_hash


@pytest.mark.asyncio
async def test_gateway_admits_environment_specific_profile_from_definition_family() -> None:
    from src.app.runtime.capability_port_registry import CapabilityPortRegistry, RuntimeCapabilityContext
    from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway
    from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.definition import DEFINITION
    from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    profile = workline_plugin_binding_service.profile_catalog.resolve(
        provider_code="WMS",
        contract_version="2026-07-06.material-flow",
        environment="production",
    )
    registry = CapabilityPortRegistry()
    registry.register(
        WmsInventoryQueryPort,
        lambda: FakeInventoryPort(
            [
                WmsInventoryItem(
                    material_code="MAT-001",
                    warehouse_code="WH-A",
                    storage_location_code="BIN-01",
                    quantity=1,
                    batch_no="LOT-2026-07",
                )
            ]
        ),
    )
    gateway = SystemCapabilityGateway(
        attempt_id="attempt-production-profile",
        definitions={(DEFINITION.capability_key, DEFINITION.contract_version): DEFINITION},
        allowed_capabilities=frozenset({(DEFINITION.capability_key, DEFINITION.contract_version)}),
        context=RuntimeCapabilityContext.from_provider_profile(registry, profile),
        admission_profile=profile.identity,
    )
    request = admission_input(
        binding_snapshot=admission_input().binding_snapshot.model_copy(update={"profile_identity": profile.identity})
    )

    result = await gateway.execute(DEFINITION.capability_key, DEFINITION.contract_version, request)

    assert isinstance(result.outcome, Success)
