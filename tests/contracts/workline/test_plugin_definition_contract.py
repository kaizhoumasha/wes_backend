from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel

from src.app.runtime.workline_plugins import WorklinePluginDefinition


class PluginConfig(BaseModel):
    enabled: bool = True


class PluginState(BaseModel):
    scans: int = 0


def parse_scan(payload: dict[str, object]) -> str:
    return str(payload["barcode"])


def build_definition(**overrides: object) -> WorklinePluginDefinition:
    values: dict[str, object] = {
        "plugin_key": "rough-sorter",
        "contract_version": "1.0.0",
        "config_model": PluginConfig,
        "state_model": PluginState,
        "routes": ("scan.received", "command.completed"),
        "allowed_capabilities": (("inventory.lookup", "1.0.0"),),
        "parsers": {"scan.received": parse_scan},
    }
    values.update(overrides)
    return WorklinePluginDefinition(**values)


def test_plugin_definition_is_frozen_deeply_immutable_and_stable() -> None:
    definition = build_definition()
    same_definition = build_definition(
        routes=("command.completed", "scan.received"),
        allowed_capabilities=(("inventory.lookup", "1.0.0"),),
    )

    assert definition.identity == same_definition.identity
    assert definition.identity.startswith("rough-sorter@1.0.0:")
    assert definition.routes == ("command.completed", "scan.received")
    assert tuple(definition.allowed_capabilities) == (("inventory.lookup", "1.0.0"),)
    with pytest.raises(FrozenInstanceError):
        definition.plugin_key = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        definition.parsers["new.route"] = parse_scan  # type: ignore[index]


@pytest.mark.parametrize("field", ["plugin_key", "contract_version"])
@pytest.mark.parametrize("value", ["", " ", "bad value", "bad/value"])
def test_plugin_definition_rejects_invalid_key_or_version(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        build_definition(**{field: value})


@pytest.mark.parametrize("field", ["config_model", "state_model"])
def test_plugin_definition_requires_pydantic_models(field: str) -> None:
    with pytest.raises(TypeError):
        build_definition(**{field: dict})


def test_plugin_routes_must_be_unique_and_declared_for_parsers() -> None:
    with pytest.raises(ValueError, match="routes must be unique"):
        build_definition(routes=("scan.received", "scan.received"))
    with pytest.raises(ValueError, match="parser route"):
        build_definition(parsers={"unknown.route": parse_scan})


def test_allowed_capabilities_require_valid_key_and_version() -> None:
    with pytest.raises(ValueError):
        build_definition(allowed_capabilities=(("", "1.0.0"),))
    with pytest.raises(ValueError):
        build_definition(allowed_capabilities=(("inventory.lookup", ""),))


def test_no_public_generic_extension_definition_exists() -> None:
    import src.app.runtime.workline_plugins.definition as definition_module

    assert not hasattr(definition_module, "ExtensionDefinition")
