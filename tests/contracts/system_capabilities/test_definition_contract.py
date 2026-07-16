from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel

from src.app.runtime.system_capabilities import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)


class QueryInput(BaseModel):
    barcode: str


class QueryOutput(BaseModel):
    accepted: bool


class InventoryPort:
    pass


class QueryHandler:
    def __call__(self) -> None:
        pass


def build_definition(**overrides: object) -> SystemCapabilityDefinition:
    values: dict[str, object] = {
        "capability_key": "inventory.lookup",
        "contract_version": "1.0.0",
        "mode": SystemCapabilityMode.QUERY,
        "input_model": QueryInput,
        "output_model": QueryOutput,
        "handler_factory": QueryHandler,
        "required_ports": (InventoryPort,),
        "admission": "runtime",
        "timeout_seconds": 3.0,
        "completion_mode": EffectCompletionMode.LOCAL_TRANSACTIONAL,
        "audit_policy": "metadata",
    }
    values.update(overrides)
    return SystemCapabilityDefinition(**values)


def test_definition_is_frozen_and_has_stable_identity() -> None:
    first = build_definition(required_ports=(InventoryPort,))
    second = build_definition(required_ports=(InventoryPort,))

    assert first.identity == second.identity
    assert first.identity.startswith("inventory.lookup@1.0.0:")
    assert len(first.identity.rsplit(":", maxsplit=1)[1]) == 64
    assert first.required_ports == (InventoryPort,)
    with pytest.raises(FrozenInstanceError):
        first.capability_key = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["capability_key", "contract_version"])
@pytest.mark.parametrize("value", ["", " ", "bad value", "bad/value"])
def test_definition_rejects_invalid_key_or_version(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        build_definition(**{field: value})


@pytest.mark.parametrize("field", ["input_model", "output_model"])
def test_definition_requires_pydantic_model_classes(field: str) -> None:
    with pytest.raises(TypeError):
        build_definition(**{field: dict})


def test_definition_saves_handler_factory_instead_of_handler_instance() -> None:
    definition = build_definition(handler_factory=QueryHandler)

    assert definition.handler_factory is QueryHandler
    assert isinstance(definition.handler_factory(), QueryHandler)
    with pytest.raises(TypeError):
        build_definition(handler_factory=QueryHandler())


def test_mode_and_completion_contracts_are_closed() -> None:
    assert set(SystemCapabilityMode) == {SystemCapabilityMode.QUERY, SystemCapabilityMode.EFFECT}
    assert set(EffectCompletionMode) == {
        EffectCompletionMode.LOCAL_TRANSACTIONAL,
        EffectCompletionMode.OUTBOX_ASYNC,
    }
    with pytest.raises(ValueError):
        build_definition(mode="stream")
    with pytest.raises(ValueError):
        build_definition(completion_mode="remote")


def test_required_ports_are_types_and_unique() -> None:
    definition = build_definition(required_ports=(InventoryPort, InventoryPort))

    assert definition.required_ports == (InventoryPort,)
    with pytest.raises(TypeError):
        build_definition(required_ports=("InventoryPort",))


def test_no_public_generic_extension_definition_exists() -> None:
    import src.app.runtime.system_capabilities.definition as definition_module

    assert not hasattr(definition_module, "ExtensionDefinition")
