from __future__ import annotations

import inspect
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType, ModuleType, SimpleNamespace

import pytest
from pydantic import BaseModel

from src.app.runtime.capabilities.material_flow.contracts.ng_reason import NgReasonDefinition, NgReasonSource
from src.app.runtime.orchestration.models.material_unit import MaterialUnitStatus
from src.app.runtime.system_capabilities import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.index_builder import (
    SystemCapabilityIndexBuilder,
    SystemCapabilitySource,
)
from src.app.runtime.workline_plugins import WorklinePluginDefinition
from src.app.runtime.workline_plugins.index_builder import (
    WorklinePluginIndexBuilder,
    WorklinePluginSource,
)
from src.app.runtime.workline_plugins.schema import (
    STATE_MACHINE_CONTRACT_PROFILES,
    DeviceRequirement,
    EventBinding,
    FlowEdge,
    NodeRef,
    PipelineQueue,
    RackPosition,
    RackPositionCarrierCapability,
    ResourceBoundary,
    SessionSubject,
    StateMachine,
    StateMachineOwner,
    StateMachineSubject,
    StateMachineTransition,
    TopologySpec,
    WorklinePluginSchema,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class QueryInput(BaseModel):
    barcode: str


class QueryOutput(BaseModel):
    accepted: bool


class PluginConfig(BaseModel):
    enabled: bool = True


class PluginState(BaseModel):
    scans: int = 0


class InventoryPort:
    pass


class UnknownPort:
    pass


SpoofedRepositoryPort = type(
    "UnregisteredPort",
    (),
    {"__module__": "src.app.evil"},
)


class QueryHandler:
    def __init__(self, inventory_port: InventoryPort) -> None:
        self.inventory_port = inventory_port

    def __call__(self, capability_input: QueryInput) -> QueryOutput:
        return QueryOutput(accepted=bool(capability_input.barcode))


class InvalidHandlerFactory:
    def __init__(self) -> None:
        pass


class WrongAnnotatedHandlerFactory:
    def __init__(self, inventory_port: UnknownPort) -> None:
        self.inventory_port = inventory_port


class MissingAnnotationHandlerFactory:
    def __init__(self, inventory_port) -> None:  # type: ignore[no-untyped-def]
        self.inventory_port = inventory_port


class VariadicHandlerFactory:
    def __init__(self, *ports: InventoryPort) -> None:
        self.ports = ports


class ExtraOptionalHandlerFactory:
    def __init__(self, inventory_port: InventoryPort, debug: bool = False) -> None:
        self.inventory_port = inventory_port
        self.debug = debug


class KeywordOnlyHandlerFactory:
    def __init__(self, *, inventory_port: InventoryPort) -> None:
        self.inventory_port = inventory_port


class NoPortHandler:
    def __call__(self, capability_input: QueryInput) -> QueryOutput:
        return QueryOutput(accepted=bool(capability_input.barcode))


def query_handler_factory(inventory_port: InventoryPort) -> QueryHandler:
    return QueryHandler(inventory_port)


def keyword_only_query_handler_factory(*, inventory_port: InventoryPort) -> QueryHandler:
    return QueryHandler(inventory_port)


def parse_scan(payload: dict[str, object]) -> str:
    return str(payload["barcode"])


def duplicate_ng_reasons() -> tuple[NgReasonDefinition, ...]:
    reason = NgReasonDefinition(
        canonical_code="DUPLICATE_NG",
        label="duplicate",
        source=NgReasonSource.PLUGIN,
        plugin_key="rough_sorter",
        contract_version="v1",
    )
    return reason, reason


def capability_definition(**overrides: object) -> SystemCapabilityDefinition:
    values: dict[str, object] = {
        "capability_key": "inventory.lookup",
        "contract_version": "v1",
        "mode": SystemCapabilityMode.QUERY,
        "input_model": QueryInput,
        "output_model": QueryOutput,
        "handler_factory": QueryHandler,
        "required_ports": (InventoryPort,),
        "admission": "wms.v1.production",
        "timeout_seconds": 3.0,
        "completion_mode": EffectCompletionMode.LOCAL_TRANSACTIONAL,
        "audit_policy": "metadata",
    }
    values.update(overrides)
    return SystemCapabilityDefinition(**values)


def plugin_definition(**overrides: object) -> WorklinePluginDefinition:
    values: dict[str, object] = {
        "plugin_key": "rough_sorter",
        "contract_version": "v1",
        "config_model": PluginConfig,
        "state_model": PluginState,
        "routes": ("scan.completed",),
        "allowed_capabilities": (("inventory.lookup", "v1"),),
        "parsers": {"scan.completed": parse_scan},
    }
    values.update(overrides)
    return WorklinePluginDefinition(**values)


def system_source(
    definition: SystemCapabilityDefinition | None = None,
    *,
    module_name: str = "tests.fixtures.system.inventory.lookup.definition",
    directory_key: str = "inventory.lookup",
) -> SystemCapabilitySource:
    return SystemCapabilitySource(
        module_name=module_name,
        directory_key=directory_key,
        definition=definition or capability_definition(),
    )


def plugin_source(
    definition: WorklinePluginDefinition | None = None,
    *,
    module_name: str = "tests.fixtures.plugins.rough_sorter.definition",
    directory_key: str = "rough_sorter",
) -> WorklinePluginSource:
    return WorklinePluginSource(
        module_name=module_name,
        directory_key=directory_key,
        definition=definition or plugin_definition(),
    )


def material_state_schema(
    *,
    category: str = "MATERIAL_UNIT",
    owner_model: str = "MaterialUnit",
    owner_field: str = "status",
    granularity: str = "MATERIAL_LIFECYCLE",
    transitions: tuple[StateMachineTransition, ...] = (
        StateMachineTransition("IN_TRANSIT", ("STORED",)),
        StateMachineTransition("STORED", ("IN_TRANSIT",)),
        StateMachineTransition("COMPLETED", ()),
        StateMachineTransition("NG", ()),
        StateMachineTransition("RECONCILING", ("IN_TRANSIT",)),
    ),
) -> WorklinePluginSchema:
    return WorklinePluginSchema(
        session_subject=SessionSubject("MATERIAL_UNIT", "REEL", ("PkgID",)),
        state_machines=(
            StateMachine(
                "material",
                StateMachineSubject(category, "MATERIAL_UNIT", "REEL"),
                StateMachineOwner(owner_model, owner_field),
                granularity,
                transitions,
            ),
        ),
    )


def system_builder() -> SystemCapabilityIndexBuilder:
    return SystemCapabilityIndexBuilder(
        known_ports=(InventoryPort,),
        known_admissions=("wms.v1.production",),
    )


def test_builders_sort_identities_and_render_stable_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    first = system_source(module_name="pkg.z.definition")
    second = system_source(
        capability_definition(
            capability_key="device.command",
            required_ports=(),
            handler_factory=NoPortHandler,
        ),
        module_name="pkg.a.definition",
        directory_key="device.command",
    )

    forward = system_builder().build((first, second))
    reverse = system_builder().build((second, first))

    assert forward.identities == reverse.identities
    assert forward.digest == reverse.digest
    assert forward.source == reverse.source
    assert forward.source.index("pkg.a.definition") < forward.source.index("pkg.z.definition")
    assert "MappingProxyType" in forward.source

    for module_name in ("pkg", "pkg.a", "pkg.z"):
        module = ModuleType(module_name)
        module.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module_name, module)
    for module_name, definition in (
        ("pkg.a.definition", second.definition),
        ("pkg.z.definition", first.definition),
    ):
        module = ModuleType(module_name)
        module.DEFINITION = definition  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module_name, module)

    namespace: dict[str, object] = {}
    exec(compile(forward.source, "generated_system_index.py", "exec"), namespace)

    generated_mapping = namespace["SYSTEM_CAPABILITY_INDEX"]
    assert isinstance(generated_mapping, MappingProxyType)
    assert namespace["SYSTEM_CAPABILITY_IDENTITIES"] == forward.identities
    assert namespace["SYSTEM_CAPABILITY_INDEX_DIGEST"] == forward.digest
    assert generated_mapping[("device.command", "v1")] is second.definition
    assert generated_mapping[("inventory.lookup", "v1")] is first.definition
    with pytest.raises(TypeError):
        generated_mapping[("new", "v1")] = first.definition  # type: ignore[index]


def test_plugin_renderer_executes_with_definition_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    first = WorklinePluginSource(
        module_name="pluginpkg.rough_sorter.definition",
        directory_key="rough_sorter",
        definition=plugin_definition(),
        handler_identities=(("scan.completed", f"{__name__}.parse_scan", f"{__name__}.PluginState"),),
    )
    second = WorklinePluginSource(
        module_name="pluginpkg.secondary.definition",
        directory_key="secondary",
        definition=plugin_definition(
            plugin_key="secondary",
            routes=("sort.completed",),
            parsers={"sort.completed": parse_scan},
        ),
        handler_identities=(("sort.completed", f"{__name__}.parse_scan", f"{__name__}.PluginState"),),
    )
    generated = WorklinePluginIndexBuilder(
        capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}
    ).build((first, second))

    for module_name in ("pluginpkg", "pluginpkg.rough_sorter", "pluginpkg.secondary"):
        module = ModuleType(module_name)
        module.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module_name, module)
    first_module = ModuleType(first.module_name)
    first_module.DEFINITION = first.definition  # type: ignore[attr-defined]
    first_module.ROUTE_HANDLERS = {  # type: ignore[attr-defined]
        ("rough_sorter", "v1", "scan.completed"): ((parse_scan, PluginState),)
    }
    monkeypatch.setitem(sys.modules, first.module_name, first_module)
    second_module = ModuleType(second.module_name)
    second_module.DEFINITION = second.definition  # type: ignore[attr-defined]
    second_module.ROUTE_HANDLERS = {  # type: ignore[attr-defined]
        ("secondary", "v1", "sort.completed"): ((parse_scan, PluginState),)
    }
    monkeypatch.setitem(sys.modules, second.module_name, second_module)

    namespace: dict[str, object] = {}
    exec(compile(generated.source, "generated_plugin_index.py", "exec"), namespace)

    generated_mapping = namespace["WORKLINE_PLUGIN_INDEX"]
    assert isinstance(generated_mapping, MappingProxyType)
    assert namespace["WORKLINE_PLUGIN_IDENTITIES"] == generated.identities
    assert namespace["WORKLINE_PLUGIN_INDEX_DIGEST"] == generated.digest
    assert generated_mapping[("rough_sorter", "v1")] is first.definition
    assert generated_mapping[("secondary", "v1")] is second.definition
    generated_registrations = namespace["WORKLINE_PLUGIN_HANDLER_REGISTRATIONS"]
    assert generated_registrations == MappingProxyType(
        {
            ("rough_sorter", "v1", "scan.completed"): ((parse_scan, PluginState),),
            ("secondary", "v1", "sort.completed"): ((parse_scan, PluginState),),
        }
    )

    import src.app.runtime.workline_plugins.handler_registry as handler_registry_module

    monkeypatch.setattr(
        handler_registry_module,
        "WORKLINE_PLUGIN_HANDLER_REGISTRATIONS",
        generated_registrations,
    )
    runtime_registry = handler_registry_module.build_generated_handler_registry(generated_mapping)
    assert set(runtime_registry) == set(generated_registrations)
    assert all(len(candidates) == 1 for candidates in runtime_registry.values())
    with pytest.raises(TypeError):
        generated_mapping[("new", "v1")] = first.definition  # type: ignore[index]


def test_plugin_digest_changes_when_static_handler_registration_changes() -> None:
    source = plugin_source()
    first = WorklinePluginSource(
        module_name=source.module_name,
        directory_key=source.directory_key,
        definition=source.definition,
        handler_identities=(("scan.completed", "pkg.handlers.decide_v1", "pkg.facts.ScanFacts"),),
    )
    second = WorklinePluginSource(
        module_name=source.module_name,
        directory_key=source.directory_key,
        definition=source.definition,
        handler_identities=(("scan.completed", "pkg.handlers.decide_v2", "pkg.facts.ScanFacts"),),
    )
    builder = WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY})

    assert builder.build((first,)).digest != builder.build((second,)).digest


def test_plugin_builder_rejects_duplicate_identity() -> None:
    first = plugin_source()
    second = plugin_source(module_name="pkg.duplicate.definition")

    with pytest.raises(ValueError, match="duplicate identity"):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (first, second)
        )


@pytest.mark.parametrize(
    ("builder", "source"),
    [
        (system_builder(), system_source(directory_key="wrong.lookup")),
        (
            WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}),
            plugin_source(directory_key="wrong_sorter"),
        ),
    ],
)
def test_builder_rejects_directory_and_definition_key_mismatch(builder: object, source: object) -> None:
    with pytest.raises(ValueError, match="directory key"):
        builder.build((source,))  # type: ignore[attr-defined]


@pytest.mark.parametrize("kind", ["system", "plugin"])
def test_builder_discovers_author_definition_files_only_at_build_time(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / kind
    definition_path = root / ("inventory/lookup" if kind == "system" else "rough_sorter") / "definition.py"
    definition_path.parent.mkdir(parents=True)
    definition_path.write_text("# author definition placeholder\n", encoding="utf-8")
    imported: list[str] = []
    definition = capability_definition() if kind == "system" else plugin_definition()

    def fake_import(module_name: str) -> object:
        imported.append(module_name)
        if kind == "plugin":
            return SimpleNamespace(
                DEFINITION=definition,
                ROUTE_HANDLERS={
                    (definition.plugin_key, definition.contract_version, "scan.completed"): (  # type: ignore[union-attr]
                        (parse_scan, PluginState),
                    )
                },
            )
        return SimpleNamespace(DEFINITION=definition)

    if kind == "system":
        import src.app.runtime.system_capabilities.index_builder as builder_module

        builder = system_builder()
        package = "fixture.system"
    else:
        import src.app.runtime.workline_plugins.index_builder as builder_module

        builder = WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY})
        package = "fixture.plugins"
    monkeypatch.setattr(builder_module.importlib, "import_module", fake_import)

    sources = builder.discover(root=root, package=package)

    assert len(sources) == 1
    assert imported == [f"{package}.{'inventory.lookup' if kind == 'system' else 'rough_sorter'}.definition"]


def test_plugin_builder_keeps_multiple_contract_versions_from_one_plugin_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.app.runtime.workline_plugins.index_builder as builder_module

    root = tmp_path / "plugins"
    definition_path = root / "rough_sorter" / "definition.py"
    definition_path.parent.mkdir(parents=True)
    definition_path.write_text("# multi-version author definition placeholder\n", encoding="utf-8")
    versions = (
        plugin_definition(contract_version="v1"),
        plugin_definition(contract_version="v2"),
    )
    route_handlers = {
        (definition.plugin_key, definition.contract_version, "scan.completed"): ((parse_scan, PluginState),)
        for definition in versions
    }
    module_name = "fixture.plugins.rough_sorter.definition"
    definition_module = ModuleType(module_name)
    definition_module.DEFINITIONS = versions  # type: ignore[attr-defined]
    definition_module.ROUTE_HANDLERS = route_handlers  # type: ignore[attr-defined]
    monkeypatch.setattr(builder_module.importlib, "import_module", lambda _module_name: definition_module)

    for package_name in ("fixture", "fixture.plugins", "fixture.plugins.rough_sorter"):
        package_module = ModuleType(package_name)
        package_module.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, package_name, package_module)
    monkeypatch.setitem(sys.modules, module_name, definition_module)

    builder = WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY})
    sources = builder.discover(root=root, package="fixture.plugins")
    generated = builder.build(sources)

    assert generated.identities == (("rough_sorter", "v1"), ("rough_sorter", "v2"))
    namespace: dict[str, object] = {}
    exec(compile(generated.source, "generated_multi_version_plugin_index.py", "exec"), namespace)
    generated_mapping = namespace["WORKLINE_PLUGIN_INDEX"]
    assert generated_mapping[("rough_sorter", "v1")] is versions[0]  # type: ignore[index]
    assert generated_mapping[("rough_sorter", "v2")] is versions[1]  # type: ignore[index]


def test_plugin_builder_rejects_unknown_capability_reference() -> None:
    builder = WorklinePluginIndexBuilder(capability_modes={})

    with pytest.raises(ValueError, match="unknown capability"):
        builder.build((plugin_source(),))


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (WorklinePluginSchema(devices=(DeviceRequirement(""),)), "non-empty identity"),
        (WorklinePluginSchema(devices=(DeviceRequirement("ARM", min_count=-1),)), "min_count"),
        (
            WorklinePluginSchema(
                rack_positions=(
                    RackPosition(
                        "A",
                        "WORK",
                        "S1",
                        RackPositionCarrierCapability(("SINGLE_LAYER",), min_capacity=-1),
                    ),
                )
            ),
            "min_capacity",
        ),
        (
            WorklinePluginSchema(
                devices=(DeviceRequirement("ARM"),),
                topology=TopologySpec(
                    (FlowEdge(NodeRef("DEVICE_ROLE", "UNKNOWN"), NodeRef("DEVICE_ROLE", "ARM"), "OPERATION"),)
                ),
            ),
            "unknown topology reference",
        ),
    ],
)
def test_plugin_builder_rejects_invalid_typed_schema(schema: WorklinePluginSchema, message: str) -> None:
    definition = plugin_definition(schema=schema)

    with pytest.raises(ValueError, match=message):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (plugin_source(definition),)
        )


def test_plugin_builder_rejects_duplicate_resource_boundary() -> None:
    boundary = ResourceBoundary("A", "SINGLE_LAYER", "SUBJECT", "OP", "PROJECTION", "STATION")
    schema = WorklinePluginSchema(
        rack_positions=(RackPosition("A", "WORK", "S1", RackPositionCarrierCapability(("SINGLE_LAYER",))),),
        resource_boundaries=(boundary, boundary),
    )

    with pytest.raises(ValueError, match="duplicate resource boundary"):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (plugin_source(plugin_definition(schema=schema)),)
        )


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (
            WorklinePluginSchema(
                devices=(DeviceRequirement("ARM"),),
                events=(EventBinding("SCAN_COMPLETED", ("ARM",), "ENTRY_DEVIC"),),
            ),
            "event category",
        ),
        (
            WorklinePluginSchema(
                devices=(DeviceRequirement("ARM"),),
                events=(EventBinding("ESTOP_PRESSED", ("ARM",), "SAFETY"),),
            ),
            "reserved runtime event",
        ),
        (WorklinePluginSchema(pipeline_queues=(PipelineQueue("", "BUFFER", 1, "FIFO"),)), "pipeline queue code"),
        (
            WorklinePluginSchema(pipeline_queues=(PipelineQueue("BUFFER", "BUFFER", -1, "FIFO"),)),
            "pipeline queue capacity",
        ),
        (
            WorklinePluginSchema(session_subject=SessionSubject("MATERIAL", "REEL", ("PkgID", "PkgID"))),
            "session subject identity_sources must be unique",
        ),
        (
            WorklinePluginSchema(session_subject=SessionSubject("MATERIAL", "REEL", ())),
            "session subject identity_sources must not be empty",
        ),
        (
            WorklinePluginSchema(
                session_subject=SessionSubject("MATERIAL_UNIT", "REEL", ("PkgID",)),
                state_machines=(
                    StateMachine(
                        "material",
                        StateMachineSubject("MATERIAL_UNIT", "MATERIAL_UNIT", "REEL"),
                        StateMachineOwner("MaterialUnit", "status"),
                        "MATERIAL_LIFECYCLE",
                        (),
                    ),
                ),
            ),
            "state machine transitions must declare an initial state",
        ),
        (
            WorklinePluginSchema(
                session_subject=SessionSubject("MATERIAL_UNIT", "REEL", ("PkgID",)),
                state_machines=(
                    StateMachine(
                        "material",
                        StateMachineSubject("MATERIAL_UNIT", "MATERIAL_UNIT", "REEL"),
                        StateMachineOwner("MaterialUnit", "status"),
                        "MATERIAL_LIFECYCLE",
                        (StateMachineTransition("IN_TRANSIT", ("STORED",)),),
                    ),
                ),
            ),
            "unknown state reference",
        ),
        (
            WorklinePluginSchema(
                session_subject=SessionSubject("MATERIAL", "REEL", ("PkgID",)),
                state_machines=(
                    StateMachine(
                        "material",
                        StateMachineSubject("MATERIAL", "OTHER", "REEL"),
                        StateMachineOwner("MaterialUnit", "status"),
                        "MATERIAL_LIFECYCLE",
                        (StateMachineTransition("IN_TRANSIT", ()),),
                    ),
                ),
            ),
            "state machine subject must match session subject",
        ),
        (
            WorklinePluginSchema(
                rack_positions=(RackPosition("A", "WORK", "S1", RackPositionCarrierCapability(("SINGLE_LAYER",))),),
                resource_boundaries=(ResourceBoundary("A", "FIVE_LAYER", "SUBJECT", "OP", "PROJECTION", "STATION"),),
            ),
            "rack_kind is not allowed",
        ),
    ],
)
def test_plugin_builder_rejects_invalid_nested_schema_contract(schema: WorklinePluginSchema, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (plugin_source(plugin_definition(schema=schema)),)
        )


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (material_state_schema(category="OTHER"), "category must equal session subject type"),
        (material_state_schema(owner_model="Anything"), "unsupported state machine contract"),
        (material_state_schema(owner_field="whatever"), "unsupported state machine contract"),
        (material_state_schema(granularity="TYPO_GRANULARITY"), "unsupported state machine contract"),
        (
            material_state_schema(transitions=(StateMachineTransition("MADE_UP", ()),)),
            "valid MaterialUnitStatus",
        ),
        (
            material_state_schema(
                transitions=(
                    StateMachineTransition("IN_TRANSIT", ("MADE_UP",)),
                    StateMachineTransition("STORED", ()),
                    StateMachineTransition("COMPLETED", ()),
                    StateMachineTransition("NG", ()),
                    StateMachineTransition("RECONCILING", ()),
                )
            ),
            "valid MaterialUnitStatus",
        ),
    ],
)
def test_plugin_builder_rejects_uncontrolled_state_machine_contract(
    schema: WorklinePluginSchema,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (plugin_source(plugin_definition(schema=schema)),)
        )


def test_plugin_builder_accepts_controlled_material_unit_state_machine_contract() -> None:
    generated = WorklinePluginIndexBuilder(
        capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}
    ).build((plugin_source(plugin_definition(schema=material_state_schema())),))

    assert generated.identities == (("rough_sorter", "v1"),)


def test_material_unit_state_machine_contract_matches_runtime_status_enum() -> None:
    profile = next(
        profile for profile in STATE_MACHINE_CONTRACT_PROFILES if profile.status_contract == "MaterialUnitStatus"
    )

    assert profile.allowed_states == frozenset(status.value for status in MaterialUnitStatus)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("routes", ("scan.completed", "scan.completed"), "duplicate route"),
        (
            "allowed_capabilities",
            (("inventory.lookup", "v1"), ("inventory.lookup", "v1")),
            "duplicate capability",
        ),
    ],
)
def test_plugin_builder_fails_closed_for_malformed_duplicate_definition_fields(
    field_name: str,
    value: object,
    message: str,
) -> None:
    definition = plugin_definition()
    object.__setattr__(definition, field_name, value)

    with pytest.raises(ValueError, match=message):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (plugin_source(definition),)
        )


def test_plugin_builder_rejects_duplicate_ng_reason() -> None:
    definition = plugin_definition(ng_reason_resolver=duplicate_ng_reasons)

    with pytest.raises(ValueError, match="duplicate NG reason"):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (plugin_source(definition),)
        )


def test_system_builder_rejects_query_outbox_completion_mismatch() -> None:
    source = system_source(capability_definition(completion_mode=EffectCompletionMode.OUTBOX_ASYNC))

    with pytest.raises(ValueError, match=r"QUERY.*LOCAL_TRANSACTIONAL"):
        system_builder().build((source,))


def test_system_builder_rejects_handler_factory_signature_mismatch() -> None:
    source = system_source(capability_definition(handler_factory=InvalidHandlerFactory))

    with pytest.raises(TypeError, match="handler_factory signature"):
        system_builder().build((source,))


@pytest.mark.parametrize(
    "handler_factory",
    [
        WrongAnnotatedHandlerFactory,
        MissingAnnotationHandlerFactory,
        VariadicHandlerFactory,
        ExtraOptionalHandlerFactory,
        KeywordOnlyHandlerFactory,
        keyword_only_query_handler_factory,
    ],
)
def test_system_builder_rejects_non_exact_handler_factory_signatures(handler_factory: object) -> None:
    source = system_source(capability_definition(handler_factory=handler_factory))

    with pytest.raises(TypeError, match="handler_factory signature"):
        system_builder().build((source,))


@pytest.mark.parametrize("handler_factory", [QueryHandler, query_handler_factory])
def test_system_builder_accepts_exact_class_and_function_factory_signatures(handler_factory: object) -> None:
    source = system_source(capability_definition(handler_factory=handler_factory))

    generated = system_builder().build((source,))

    assert generated.identities == (("inventory.lookup", "v1"),)


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        (capability_definition(required_ports=(UnknownPort,)), "unknown Port"),
        (capability_definition(admission="unknown-profile"), "unknown admission/profile"),
    ],
)
def test_system_builder_rejects_unknown_port_or_profile(
    definition: SystemCapabilityDefinition,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        system_builder().build((system_source(definition),))


@pytest.mark.parametrize(
    "definition",
    [
        capability_definition(required_ports=(UnknownPort,)),
        capability_definition(admission="unknown-profile"),
    ],
)
def test_default_system_builder_fails_closed_for_unknown_repository_contracts(
    definition: SystemCapabilityDefinition,
) -> None:
    with pytest.raises(ValueError, match="unknown"):
        SystemCapabilityIndexBuilder().build((system_source(definition),))


def test_default_system_builder_rejects_generic_provider_contract_admission() -> None:
    definition = capability_definition(
        admission="provider-contract",
        required_ports=(),
        handler_factory=NoPortHandler,
    )

    with pytest.raises(ValueError, match="unknown admission"):
        SystemCapabilityIndexBuilder().build((system_source(definition),))


def test_default_port_catalog_rejects_module_spoofed_unregistered_port() -> None:
    definition = capability_definition(required_ports=(SpoofedRepositoryPort,))

    with pytest.raises(ValueError, match="unknown Port"):
        SystemCapabilityIndexBuilder().build((system_source(definition),))


def test_explicit_port_catalog_accepts_only_registered_port() -> None:
    definition = capability_definition(required_ports=(InventoryPort,))

    generated = SystemCapabilityIndexBuilder(
        known_ports=(InventoryPort,),
        known_admissions=("wms.v1.production",),
    ).build((system_source(definition),))

    assert generated.identities == (("inventory.lookup", "v1"),)


def test_generated_indexes_are_complete_read_only_and_cold_start_safe() -> None:
    import src.app.runtime.system_capabilities.generated_index as system_index
    import src.app.runtime.workline_plugins.generated_index as plugin_index

    assert isinstance(system_index.SYSTEM_CAPABILITY_INDEX, MappingProxyType)
    assert isinstance(plugin_index.WORKLINE_PLUGIN_INDEX, MappingProxyType)
    assert system_index.SYSTEM_CAPABILITY_IDENTITIES == (
        ("device.device_command_write", "v1"),
        ("material_flow.material_unit_write", "v1"),
        ("runtime.session_hold", "v1"),
        ("wms.inventory.query_inventory", "v1"),
    )
    assert plugin_index.WORKLINE_PLUGIN_IDENTITIES == (("rough_sorter", "rough_sorter.v2"),)
    assert tuple(plugin_index.WORKLINE_PLUGIN_INDEX) == (("rough_sorter", "rough_sorter.v2"),)
    with pytest.raises(TypeError):
        system_index.SYSTEM_CAPABILITY_INDEX[("x", "v1")] = object()  # type: ignore[index]

    for module in (system_index, plugin_index):
        source = inspect.getsource(module)
        assert "glob(" not in source
        assert "rglob(" not in source
        assert "import_module(" not in source
        assert "__import__(" not in source


def test_cli_write_is_idempotent_and_check_reports_both_indexes(tmp_path: Path) -> None:
    plugin_output = tmp_path / "plugin_index.py"
    system_output = tmp_path / "system_index.py"
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts/generate_runtime_extensions.py"),
        "--plugin-output",
        str(plugin_output),
        "--system-output",
        str(system_output),
    ]

    first = subprocess.run(command, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    first_bytes = (plugin_output.read_bytes(), system_output.read_bytes())
    second = subprocess.run(command, cwd=REPO_ROOT, check=True, capture_output=True, text=True)

    assert first.stdout == second.stdout
    assert first_bytes == (plugin_output.read_bytes(), system_output.read_bytes())
    check = subprocess.run([*command, "--check"], cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    assert check.returncode == 0
    assert "workline_plugins: count=1 digest=" in check.stdout
    assert "system_capabilities: count=4 digest=" in check.stdout


def test_cli_check_detects_drift_without_overwriting_file(tmp_path: Path) -> None:
    plugin_output = tmp_path / "plugin_index.py"
    system_output = tmp_path / "system_index.py"
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts/generate_runtime_extensions.py"),
        "--plugin-output",
        str(plugin_output),
        "--system-output",
        str(system_output),
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True)
    plugin_output.write_text("# deliberate drift\n", encoding="utf-8")

    result = subprocess.run([*command, "--check"], cwd=REPO_ROOT, check=False, capture_output=True, text=True)

    assert result.returncode != 0
    assert "drift" in result.stderr.lower()
    assert plugin_output.read_text(encoding="utf-8") == "# deliberate drift\n"


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("alias_kind", ["literal", "normalized", "symlink", "hardlink"])
def test_cli_rejects_same_destination_alias_without_modifying_file(
    tmp_path: Path,
    alias_kind: str,
    check: bool,
) -> None:
    from scripts import generate_runtime_extensions as generator

    destination = tmp_path / "shared_index.py"
    destination.write_text("preserve me\n", encoding="utf-8")
    if alias_kind == "literal":
        alias = destination
    elif alias_kind == "normalized":
        (tmp_path / "nested").mkdir()
        alias = tmp_path / "nested" / ".." / destination.name
    elif alias_kind == "symlink":
        alias = tmp_path / "symlink_index.py"
        alias.symlink_to(destination)
    else:
        alias = tmp_path / "hardlink_index.py"
        os.link(destination, alias)

    with pytest.raises(ValueError, match="distinct"):
        generator.generate(plugin_output=destination, system_output=alias, check=check)

    assert destination.read_text(encoding="utf-8") == "preserve me\n"
    assert alias.read_text(encoding="utf-8") == "preserve me\n"


@pytest.mark.parametrize("check", [False, True])
def test_cli_honors_filesystem_case_semantics_for_initially_missing_destinations(
    tmp_path: Path,
    check: bool,
) -> None:
    from scripts import generate_runtime_extensions as generator

    case_probe = tmp_path / "CaseProbe"
    case_probe.write_text("probe", encoding="utf-8")
    case_insensitive = (tmp_path / "caseprobe").exists()
    case_probe.unlink()
    plugin_output = tmp_path / "Plugin.py"
    system_output = tmp_path / "plugin.py"
    assert not plugin_output.exists()
    assert not system_output.exists()

    if case_insensitive:
        with pytest.raises(ValueError, match="distinct"):
            generator.generate(plugin_output=plugin_output, system_output=system_output, check=check)
        assert not plugin_output.exists()
        assert not system_output.exists()
    else:
        result = generator.generate(plugin_output=plugin_output, system_output=system_output, check=check)
        assert result == int(check)
        assert plugin_output.exists() is not check
        assert system_output.exists() is not check


def test_cli_preserves_existing_output_mode_and_uses_umask_for_new_output(tmp_path: Path) -> None:
    from scripts import generate_runtime_extensions as generator

    plugin_output = tmp_path / "plugin_index.py"
    system_output = tmp_path / "system_index.py"
    plugin_output.write_text("old plugin\n", encoding="utf-8")
    plugin_output.chmod(0o640)
    current_umask = os.umask(0)
    os.umask(current_umask)

    generator.generate(plugin_output=plugin_output, system_output=system_output, check=False)

    assert stat.S_IMODE(plugin_output.stat().st_mode) == 0o640
    assert stat.S_IMODE(system_output.stat().st_mode) == 0o666 & ~current_umask


def test_cli_atomic_write_failure_preserves_previous_generated_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import generate_runtime_extensions as generator

    plugin_output = tmp_path / "plugin_index.py"
    system_output = tmp_path / "system_index.py"
    plugin_output.write_text("old plugin\n", encoding="utf-8")
    system_output.write_text("old system\n", encoding="utf-8")

    real_replace = generator._replace_one
    replacement_count = 0

    def fail_second_replace(temporary_path: Path, destination: Path) -> None:
        nonlocal replacement_count
        replacement_count += 1
        if replacement_count == 2:
            raise OSError("simulated replace failure")
        real_replace(temporary_path, destination)

    monkeypatch.setattr(generator, "_replace_one", fail_second_replace)

    with pytest.raises(OSError, match="simulated"):
        generator.generate(plugin_output=plugin_output, system_output=system_output, check=False)

    assert plugin_output.read_text(encoding="utf-8") == "old plugin\n"
    assert system_output.read_text(encoding="utf-8") == "old system\n"
