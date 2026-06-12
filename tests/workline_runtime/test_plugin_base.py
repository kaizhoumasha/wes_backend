from concurrent.futures import ThreadPoolExecutor
from time import sleep
from types import SimpleNamespace

import pytest

import src.workline_plugin_registry as registry
from src.workline_plugin_registry import WorklinePluginDefinition
from src.workline_runtime.orchestrator import OrchestratorService
from src.workline_runtime.plugin_base import WorklinePlugin, build_payload_invalid_block, on_event
from src.workline_runtime.plugin_manifest import (
    DeviceRequirement,
    Position,
    PositionCarrierCapability,
    TopologySpec,
    WorklinePluginManifest,
)
from src.workline_runtime.runtime_intent import RuntimeIntent, RuntimeIntentKind


class RuntimeNativePlugin(WorklinePlugin):
    plugin_key = "runtime_native"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event):
        return ctx.next.update_context({"pkg_id": event.payload_json["data"]["PkgID"]})


class InvalidReturnPlugin(WorklinePlugin):
    plugin_key = "invalid_return"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event):
        return "bad"


class RegistrySingletonPlugin(WorklinePlugin):
    plugin_key = "registry_singleton"
    manifest = WorklinePluginManifest(
        plugin_key=plugin_key,
        contract_version="test.v1",
        devices=(DeviceRequirement(role="SCANNER", min_count=0),),
        positions=(
            Position(
                code="ENTRY",
                role="ENTRY",
                station_code="ST-1",
                carrier_capability=PositionCarrierCapability(allowed_rack_kinds=("SINGLE_LAYER",)),
            ),
        ),
        topology=TopologySpec(),
    )


class SlowRegistrySingletonPlugin(WorklinePlugin):
    plugin_key = "slow_registry_singleton"
    instances_created = 0

    def __init__(self) -> None:
        sleep(0.01)
        type(self).instances_created += 1


class LegacyContextModel:
    pass


class RuntimeContextModel:
    pass


class LegacyContextModelOnlyPlugin(WorklinePlugin):
    plugin_key = "legacy_context_model_only"
    context_model = LegacyContextModel


class RuntimeContextModelPlugin(WorklinePlugin):
    plugin_key = "runtime_context_model"

    def get_context_model(self) -> type[RuntimeContextModel]:
        return RuntimeContextModel


def _ctx() -> SimpleNamespace:
    from src.workline_runtime.plugin_next import PluginNext

    return SimpleNamespace(
        next=PluginNext(),
        logger=SimpleNamespace(warning=lambda *_: None, error=lambda *_: None, exception=lambda *_: None),
        normalized_input=None,
        trace_id="trace-test",
    )


def _inbox(event_type: str = "SCAN_COMPLETED") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        payload_json={
            "event_type": event_type,
            "data": {"PkgID": "L0001-1"},
        },
    )


@pytest.mark.asyncio
async def test_handler_returns_runtime_intent_list() -> None:
    intents = await RuntimeNativePlugin().on_device_event(_ctx(), _inbox())

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.UPDATE_CONTEXT]
    assert intents[0].context_patch == {"pkg_id": "L0001-1"}


@pytest.mark.asyncio
async def test_missing_handler_returns_empty_intents() -> None:
    intents = await RuntimeNativePlugin().on_device_event(_ctx(), _inbox("UNKNOWN_EVENT"))

    assert intents == []


@pytest.mark.asyncio
async def test_invalid_handler_return_is_rejected() -> None:
    with pytest.raises(TypeError, match="Plugin handler must return RuntimeIntent"):
        await InvalidReturnPlugin().on_device_event(_ctx(), _inbox())


def test_build_payload_invalid_block_returns_block_intent() -> None:
    intent = build_payload_invalid_block("缺少 PkgID")

    assert isinstance(intent, RuntimeIntent)
    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.reason_code == "PAYLOAD_INVALID"


def test_registry_definition_returns_single_plugin_instance() -> None:
    definition = WorklinePluginDefinition(
        plugin_key="registry_singleton",
        plugin_module=__name__,
        plugin_class_name="RegistrySingletonPlugin",
    )

    first = definition.plugin_instance
    second = definition.plugin_instance

    assert first is second


def test_registry_definition_returns_single_plugin_instance_under_concurrency() -> None:
    SlowRegistrySingletonPlugin.instances_created = 0
    definition = WorklinePluginDefinition(
        plugin_key="slow_registry_singleton",
        plugin_module=__name__,
        plugin_class_name="SlowRegistrySingletonPlugin",
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        instances = list(executor.map(lambda _: definition.plugin_instance, range(16)))

    assert len({id(instance) for instance in instances}) == 1
    assert SlowRegistrySingletonPlugin.instances_created == 1


def test_registry_context_model_helper_ignores_legacy_field() -> None:
    plugin_key = "legacy_context_model_only"
    old_definition = registry.WORKLINE_PLUGIN_REGISTRY.get(plugin_key)
    registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = WorklinePluginDefinition(
        plugin_key=plugin_key,
        plugin_module=__name__,
        plugin_class_name="LegacyContextModelOnlyPlugin",
    )
    try:
        assert registry.get_workline_context_model(plugin_key) is None
    finally:
        if old_definition is None:
            registry.WORKLINE_PLUGIN_REGISTRY.pop(plugin_key, None)
        else:
            registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = old_definition


def test_registry_context_model_helper_uses_runtime_method() -> None:
    plugin_key = "runtime_context_model"
    old_definition = registry.WORKLINE_PLUGIN_REGISTRY.get(plugin_key)
    registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = WorklinePluginDefinition(
        plugin_key=plugin_key,
        plugin_module=__name__,
        plugin_class_name="RuntimeContextModelPlugin",
    )
    try:
        assert registry.get_workline_context_model(plugin_key) is RuntimeContextModel
    finally:
        if old_definition is None:
            registry.WORKLINE_PLUGIN_REGISTRY.pop(plugin_key, None)
        else:
            registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = old_definition


def test_orchestrator_load_plugin_returns_registry_cached_instance() -> None:
    plugin_key = "registry_singleton"
    definition = WorklinePluginDefinition(
        plugin_key=plugin_key,
        plugin_module=__name__,
        plugin_class_name="RegistrySingletonPlugin",
    )
    old_definition = registry.WORKLINE_PLUGIN_REGISTRY.get(plugin_key)
    registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = definition
    try:
        loaded = OrchestratorService()._load_plugin(RegistrySingletonPlugin)
    finally:
        if old_definition is None:
            registry.WORKLINE_PLUGIN_REGISTRY.pop(plugin_key, None)
        else:
            registry.WORKLINE_PLUGIN_REGISTRY[plugin_key] = old_definition

    assert loaded is definition.plugin_instance
