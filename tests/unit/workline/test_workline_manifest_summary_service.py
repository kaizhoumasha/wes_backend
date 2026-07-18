"""WorkLine manifest summary service regression tests."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from src.app.workline.models.workline import WorkLinePluginOption
from src.app.workline.services.workline_service import WorkLineService

workline_service_module = importlib.import_module("src.app.workline.services.workline_service")


def test_plugin_definition_summary_accepts_raw_iterable_fields(monkeypatch) -> None:
    """manifest 摘要构建必须接受 tuple/list 等 raw iterable 字段。"""

    schema = SimpleNamespace(
        devices=(
            SimpleNamespace(
                role="SCAN",
                min_count=1,
                max_count=None,
                hardware_capabilities=("BARCODE", "RFID"),
            ),
        ),
        rack_positions=(
            SimpleNamespace(
                code="RACK-POS-01",
                role="WORK",
                station_code="STATION-01",
                carrier_capability=SimpleNamespace(
                    allowed_rack_kinds=("SINGLE_LAYER",),
                    min_capacity=1,
                    max_capacity=2,
                    allowed_slot_kinds=("TRAY", "REEL"),
                ),
            ),
        ),
        topology=SimpleNamespace(
            flow_edges=(
                SimpleNamespace(
                    from_node=SimpleNamespace(kind="DEVICE", ref="SCAN"),
                    to_node=SimpleNamespace(kind="RACK_POSITION", ref="RACK-POS-01"),
                    type="MATERIAL_FLOW",
                ),
            )
        ),
        events=(
            SimpleNamespace(
                event="SCAN_COMPLETED",
                source_device_roles=("SCAN", "SCALE"),
                category="DEVICE_EVENT",
            ),
        ),
        commands=(SimpleNamespace(command="MOVE_RACK", target_device_role="RCS"),),
        resource_boundaries=(
            SimpleNamespace(
                rack_position_code="RACK-POS-01",
                rack_kind="SINGLE_LAYER",
                business_demand_type="SUPPLY",
                wms_operation_type="SUPPLY_SINGLE_LAYER_RACK",
                snapshot_kind="ACTIVE_RACK",
                lease_scope="STATION",
            ),
        ),
        session_subject=SimpleNamespace(
            type="PACKAGE",
            physical_form="BOX",
            identity_sources=("pkg_code", "pallet_id"),
        ),
        state_machines=(
            SimpleNamespace(
                id="package-state",
                subject=SimpleNamespace(category="MATERIAL", type="PACKAGE", physical_form="BOX"),
                state_owner=SimpleNamespace(model="WorklineSession", field="status"),
                granularity="SESSION",
                transitions=(SimpleNamespace(from_state="CREATED", to_states=("RUNNING", "BLOCKED")),),
            ),
        ),
        pipeline_queues=(),
    )
    definition = SimpleNamespace(plugin_key="iterable_definition", contract_version="2026-07-10", schema=schema)
    monkeypatch.setattr(
        workline_service_module,
        "get_workline_capability_definition",
        lambda plugin_key, contract_version=None: definition if plugin_key == "iterable_definition" else None,
    )

    summary = WorkLineService().get_plugin_definition_summary("iterable_definition")

    assert summary is not None
    assert summary.devices[0].hardware_capabilities == ["BARCODE", "RFID"]
    assert summary.rack_positions[0].carrier_capability.allowed_rack_kinds == ["SINGLE_LAYER"]
    assert summary.rack_positions[0].carrier_capability.allowed_slot_kinds == ["TRAY", "REEL"]
    assert summary.events[0].source_device_roles == ["SCAN", "SCALE"]
    assert summary.session_subject is not None
    assert summary.session_subject.identity_sources == ["pkg_code", "pallet_id"]
    assert summary.state_machines[0].transitions[0].to_states == ["RUNNING", "BLOCKED"]


def test_plugin_definition_summary_queries_exact_contract_version(monkeypatch) -> None:
    seen: list[tuple[str, str | None]] = []

    def get_definition(plugin_key: str, contract_version: str | None = None) -> None:
        seen.append((plugin_key, contract_version))

    monkeypatch.setattr(workline_service_module, "get_workline_capability_definition", get_definition)

    assert WorkLineService().get_plugin_definition_summary("demo", "v2") is None
    assert seen == [("demo", "v2")]


def test_plugin_options_aggregate_contract_versions_by_plugin_key(monkeypatch) -> None:
    definitions = [
        SimpleNamespace(plugin_key="demo", contract_version="v3"),
        SimpleNamespace(plugin_key="demo", contract_version="v2"),
        SimpleNamespace(plugin_key="other", contract_version="v1"),
    ]
    monkeypatch.setattr(workline_service_module, "list_workline_capability_definitions", lambda: definitions)

    options = WorkLineService().list_plugin_options()

    assert options == [
        WorkLinePluginOption(
            plugin_key="demo",
            label="demo",
            contract_versions=["v2", "v3"],
            default_contract_version="v3",
        ),
        WorkLinePluginOption(
            plugin_key="other",
            label="other",
            contract_versions=["v1"],
            default_contract_version="v1",
        ),
    ]


def test_configuration_checks_query_configured_contract_version(monkeypatch) -> None:
    seen: list[tuple[str | None, str | None]] = []

    def get_definition(plugin_key: str | None, contract_version: str | None = None) -> None:
        seen.append((plugin_key, contract_version))

    monkeypatch.setattr(workline_service_module, "get_workline_capability_definition", get_definition)
    workline = SimpleNamespace(plugin_key="demo", contract_version="v2")

    checks = WorkLineService()._build_configuration_checks(workline, [])

    assert checks[0].code == "PLUGIN_CONFIGURED"
    assert seen == [("demo", "v2")]


def test_plugin_key_validation_queries_explicit_contract_version(monkeypatch) -> None:
    seen: list[tuple[str | None, str | None]] = []
    definition = SimpleNamespace(schema=SimpleNamespace())

    def get_definition(plugin_key: str | None, contract_version: str | None = None) -> object:
        seen.append((plugin_key, contract_version))
        return definition

    monkeypatch.setattr(workline_service_module, "get_workline_capability_definition", get_definition)

    WorkLineService._validate_plugin_key("demo", "v2")

    assert seen == [("demo", "v2")]
