from __future__ import annotations

import pytest

from src.app.runtime.system_capabilities import SystemCapabilityMode
from src.app.runtime.workline_plugins.index_builder import (
    WorklinePluginIndexBuilder,
    WorklinePluginSource,
    workline_plugin_handler_identities,
)
from tests.workline_runtime.extensions.test_runtime_extension_index_generation import (
    PluginState,
    build_plugin_facts,
    parse_scan,
    plugin_definition,
    plugin_source,
)


def test_plugin_digest_changes_when_static_handler_registration_changes() -> None:
    source = plugin_source()
    first = WorklinePluginSource(
        module_name=source.module_name,
        directory_key=source.directory_key,
        definition=source.definition,
        handler_identities=(("scan.completed", "pkg.handlers.decide_v1", "pkg.facts.ScanFacts", "pkg.facts.build_v1"),),
    )
    second = WorklinePluginSource(
        module_name=source.module_name,
        directory_key=source.directory_key,
        definition=source.definition,
        handler_identities=(("scan.completed", "pkg.handlers.decide_v2", "pkg.facts.ScanFacts", "pkg.facts.build_v1"),),
    )
    builder = WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY})

    assert builder.build((first,)).digest != builder.build((second,)).digest


def test_plugin_digest_changes_when_facts_builder_registration_changes() -> None:
    source = plugin_source()
    first = WorklinePluginSource(
        module_name=source.module_name,
        directory_key=source.directory_key,
        definition=source.definition,
        handler_identities=(("scan.completed", "pkg.handlers.decide", "pkg.facts.ScanFacts", "pkg.facts.build_v1"),),
    )
    second = WorklinePluginSource(
        module_name=source.module_name,
        directory_key=source.directory_key,
        definition=source.definition,
        handler_identities=(("scan.completed", "pkg.handlers.decide", "pkg.facts.ScanFacts", "pkg.facts.build_v2"),),
    )
    builder = WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY})

    assert builder.build((first,)).digest != builder.build((second,)).digest


def test_plugin_registration_identity_rejects_lambda_missing_and_duplicate_routes() -> None:
    definition = plugin_definition()
    route_key = (definition.plugin_key, definition.contract_version, "scan.completed")

    with pytest.raises(TypeError, match="stable import identity"):
        workline_plugin_handler_identities(
            definition,
            {route_key: ((lambda _payload: None, PluginState, build_plugin_facts),)},
        )
    with pytest.raises(ValueError, match="exactly one handler registration"):
        workline_plugin_handler_identities(definition, {})
    with pytest.raises(ValueError, match="exactly one handler registration"):
        workline_plugin_handler_identities(
            definition,
            {route_key: ((parse_scan, PluginState, build_plugin_facts), (parse_scan, PluginState, build_plugin_facts))},
        )


def test_plugin_builder_rejects_duplicate_identity() -> None:
    first = plugin_source()
    second = plugin_source(module_name="pkg.duplicate.definition")

    with pytest.raises(ValueError, match="duplicate identity"):
        WorklinePluginIndexBuilder(capability_modes={("inventory.lookup", "v1"): SystemCapabilityMode.QUERY}).build(
            (first, second)
        )
