from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
from pydantic import BaseModel

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


class QueryHandler:
    def __init__(self, inventory_port: InventoryPort) -> None:
        self.inventory_port = inventory_port

    def __call__(self, capability_input: QueryInput) -> QueryOutput:
        return QueryOutput(accepted=bool(capability_input.barcode))


class InvalidHandlerFactory:
    def __init__(self) -> None:
        pass


class NoPortHandler:
    def __call__(self, capability_input: QueryInput) -> QueryOutput:
        return QueryOutput(accepted=bool(capability_input.barcode))


def parse_scan(payload: dict[str, object]) -> str:
    return str(payload["barcode"])


def capability_definition(**overrides: object) -> SystemCapabilityDefinition:
    values: dict[str, object] = {
        "capability_key": "inventory.lookup",
        "contract_version": "v1",
        "mode": SystemCapabilityMode.QUERY,
        "input_model": QueryInput,
        "output_model": QueryOutput,
        "handler_factory": QueryHandler,
        "required_ports": (InventoryPort,),
        "admission": "provider-contract",
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


def system_builder() -> SystemCapabilityIndexBuilder:
    return SystemCapabilityIndexBuilder(
        known_ports=(InventoryPort,),
        known_admissions=("provider-contract",),
    )


def test_builders_sort_identities_and_render_stable_digest() -> None:
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
    compile(forward.source, "generated_system_index.py", "exec")


@pytest.mark.parametrize("kind", ["identity", "route"])
def test_plugin_builder_rejects_duplicate_identity_or_route(kind: str) -> None:
    first = plugin_source()
    if kind == "identity":
        second = plugin_source(module_name="pkg.duplicate.definition")
    else:
        second = plugin_source(
            plugin_definition(plugin_key="secondary"),
            module_name="pkg.secondary.definition",
            directory_key="secondary",
        )

    with pytest.raises(ValueError, match=f"duplicate {kind}"):
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


def test_plugin_builder_rejects_unknown_capability_reference() -> None:
    builder = WorklinePluginIndexBuilder(capability_modes={})

    with pytest.raises(ValueError, match="unknown capability"):
        builder.build((plugin_source(),))


def test_system_builder_rejects_query_outbox_completion_mismatch() -> None:
    source = system_source(capability_definition(completion_mode=EffectCompletionMode.OUTBOX_ASYNC))

    with pytest.raises(ValueError, match=r"QUERY.*LOCAL_TRANSACTIONAL"):
        system_builder().build((source,))


def test_system_builder_rejects_handler_factory_signature_mismatch() -> None:
    source = system_source(capability_definition(handler_factory=InvalidHandlerFactory))

    with pytest.raises(TypeError, match="handler_factory signature"):
        system_builder().build((source,))


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


def test_generated_indexes_are_empty_read_only_and_cold_start_safe() -> None:
    import src.app.runtime.system_capabilities.generated_index as system_index
    import src.app.runtime.workline_plugins.generated_index as plugin_index

    assert isinstance(system_index.SYSTEM_CAPABILITY_INDEX, MappingProxyType)
    assert isinstance(plugin_index.WORKLINE_PLUGIN_INDEX, MappingProxyType)
    assert system_index.SYSTEM_CAPABILITY_IDENTITIES == ()
    assert plugin_index.WORKLINE_PLUGIN_IDENTITIES == ()
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
    assert "workline_plugins: count=0 digest=" in check.stdout
    assert "system_capabilities: count=0 digest=" in check.stdout


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
