"""RuntimeInbox attempt 的 pinned provider profile 与 Port 方法准入测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.runtime.capability_port_registry import CapabilityPortRegistry
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
    _configure_attempt_runtime_ports,
    _runtime_profile_from_pinned_binding,
)
from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.outcomes import RetryableFailure, Success
from src.app.runtime.workline_plugins.attempt_coordinator import WriteDisposition
from src.app.runtime.workline_plugins.contracts import PluginDecision
from src.app.runtime.workline_plugins.dispatcher import (
    PinnedPluginSnapshot,
    PluginAttemptFactSource,
    PluginDispatchRequest,
)
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryQueryOperationPort,
    InventoryQueryOperationRequest,
)
from src.app.workline import runtime_services as runtime_services_module
from src.app.workline.runtime_services import build_workline_runtime_services
from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service


class _QueryInput(BaseModel):
    value: int


class _QueryOutput(BaseModel):
    value: int


class _ImmediateQueryHandler:
    async def __call__(self, request: _QueryInput) -> _QueryOutput:
        return _QueryOutput(value=request.value)


class _ApprovedInventoryQueryHandler:
    def __init__(self, inventory_port: object) -> None:
        self._inventory_port = inventory_port

    async def __call__(self, request: _QueryInput) -> _QueryOutput:
        await self._inventory_port.execute(value=request.value)
        return _QueryOutput(value=request.value)


class _BlockedInventoryQueryHandler:
    def __init__(self, inventory_port: object) -> None:
        self._inventory_port = inventory_port

    async def __call__(self, request: _QueryInput) -> _QueryOutput:
        await self._inventory_port.undeclared_query(value=request.value)
        return _QueryOutput(value=request.value)


class _PluginState(BaseModel):
    step: int = 1


class _InventoryPort:
    async def execute(self, **_kwargs: object) -> object:
        return object()

    async def undeclared_query(self, **_kwargs: object) -> object:
        return object()


def _profile() -> ExternalContractProfile:
    return ExternalContractProfile(
        provider_code="ALTERNATE",
        contract_version="v1",
        environment="sandbox",
        timeout_retry_query_timeout_seconds=1,
        timeout_retry_retry_backoff_seconds=[1],
        fixture_set_path="tests/fixtures/external_contracts/alternate/default",
        fixture_set_required_cases=["success"],
    )


@pytest.mark.asyncio
async def test_attempt_runtime_uses_pinned_provider_profile_identity() -> None:
    """合法的非粗分机 profile 必须作为 Gateway admission 身份。"""

    definition = SystemCapabilityDefinition(
        capability_key="inventory.lookup",
        contract_version="v1",
        mode=SystemCapabilityMode.QUERY,
        input_model=_QueryInput,
        output_model=_QueryOutput,
        handler_factory=_ImmediateQueryHandler,
        required_ports=(),
        admission="alternate.v1",
        timeout_seconds=1,
        completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
        audit_policy="metadata",
    )
    runtime = RuntimeInboxProcessorBridge().create_attempt_runtime(
        "lease-alternate-profile",
        definitions={("inventory.lookup", "v1"): definition},
        provider_profile=_profile(),
    )

    result = await runtime.gateway.execute("inventory.lookup", "v1", {"value": 7})

    assert isinstance(result.outcome, Success)


def test_attempt_runtime_exposes_registered_typed_port_contract() -> None:
    """RuntimeCapabilityContext 只负责 typed port 注入，不再维护字符串方法清单。"""

    registry = CapabilityPortRegistry()
    registry.register(InventoryQueryOperationPort, _InventoryPort)
    runtime = RuntimeInboxProcessorBridge().create_attempt_runtime(
        "lease-method-allowlist",
        base_registry=registry,
        definitions={},
        provider_profile=_profile(),
    )
    inventory_port = runtime.context.get_query_port(InventoryQueryOperationPort)

    assert callable(inventory_port.execute)
    assert callable(inventory_port.undeclared_query)


def test_attempt_runtime_registers_typed_inventory_factory_without_caching_instances() -> None:
    registry = CapabilityPortRegistry()
    runtime = SimpleNamespace(port_registry=registry)

    _configure_attempt_runtime_ports(
        runtime,
        services=SimpleNamespace(
            inventory_query_port_factory=lambda: _InventoryPort,
        ),
    )

    assert registry.get(InventoryQueryOperationPort) is not registry.get(InventoryQueryOperationPort)


@pytest.mark.asyncio
async def test_real_runtime_services_fail_closed_until_compiled_query_endpoint_is_injected() -> None:
    """Task 2 仅负责编译 endpoint。

    T3 注入 composition root 前，真实 QUERY runtime 必须 fail closed。
    """

    runtime = SimpleNamespace(port_registry=CapabilityPortRegistry())
    services = build_workline_runtime_services(
        db=object(),
        session=SimpleNamespace(run_mode="NORMAL"),
    )
    _configure_attempt_runtime_ports(runtime, services=services)
    inventory_port = runtime.port_registry.get(InventoryQueryOperationPort)

    with pytest.raises(RuntimeError, match=r"compiled WMS QUERY endpoint injection.*T3"):
        await inventory_port.execute(InventoryQueryOperationRequest(material_code="MAT-001"))


def test_simulation_run_mode_requires_enabled_deployment_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime_services_module,
        "settings",
        SimpleNamespace(WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=False),
        raising=False,
    )

    with pytest.raises(ValueError, match=r"simulation.*disabled"):
        build_workline_runtime_services(
            db=object(),
            session=SimpleNamespace(run_mode="SIMULATION"),
        )


def test_pinned_binding_profile_uses_exact_snapshot_identity() -> None:
    """attempt profile 必须来自 binding snapshot 的精确 identity。"""

    profile = _profile()
    binding = SimpleNamespace(
        typed_config_json={"provider_profile": profile.identity},
        provider_profile_snapshot_json=[profile.model_dump(mode="json")],
    )

    pinned = _runtime_profile_from_pinned_binding(binding, expected_identity=profile.identity)

    assert pinned.identity == profile.identity


@pytest.mark.asyncio
async def test_platform_attempt_pins_runtime_to_binding_profile_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 2 前必须用 dispatch pin 对应的 binding profile 替换 runtime admission。"""

    profile = _profile()
    binding = SimpleNamespace(
        id=17,
        binding_version=4,
        typed_config_hash="a" * 64,
        generated_index_digest="b" * 64,
        typed_config_json={"provider_profile": profile.identity},
        provider_profile_snapshot_json=[profile.model_dump(mode="json")],
    )
    monkeypatch.setattr(workline_plugin_binding_service, "get_pinned", AsyncMock(return_value=binding))
    registry = CapabilityPortRegistry()
    registry.register(InventoryQueryOperationPort, _InventoryPort)
    bridge = RuntimeInboxProcessorBridge()
    runtime = bridge.create_attempt_runtime("lease-pinned", base_registry=registry)
    snapshot = PinnedPluginSnapshot(
        plugin_key="plugin",
        contract_version="v1",
        binding_identity="binding:17:4",
        binding_id=17,
        binding_version=4,
        config_hash="a" * 64,
        index_digest="b" * 64,
        profile_identity=profile.identity,
    )

    await bridge._pin_attempt_runtime_to_dispatch_snapshot(object(), runtime=runtime, snapshot=snapshot)

    assert runtime.gateway._admission_profile == profile.identity
    inventory_port = runtime.context.get_query_port(InventoryQueryOperationPort)
    assert callable(inventory_port.execute)
    assert callable(inventory_port.undeclared_query)


@pytest.mark.asyncio
async def test_process_claimed_uses_pinned_profile_before_generated_stage_two_and_closes_gateways(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产 wiring 在 QUERY 前 pin profile，并由 claim owner 关闭新旧 Gateway。"""

    from src.app.runtime.orchestration.services.runtime_inbox import runtime_inbox_orchestrator_bridge as module
    from src.app.runtime.system_capabilities import generated_index as capability_index_module

    profile = _profile()
    binding = SimpleNamespace(
        id=17,
        binding_version=4,
        typed_config_hash="a" * 64,
        generated_index_digest="b" * 64,
        typed_config_json={"provider_profile": profile.identity},
        provider_profile_snapshot_json=[profile.model_dump(mode="json")],
    )
    snapshot = PinnedPluginSnapshot(
        plugin_key="plugin",
        contract_version="v1",
        binding_identity="binding:17:4",
        binding_id=17,
        binding_version=4,
        config_hash="a" * 64,
        index_digest="b" * 64,
        profile_identity=profile.identity,
    )
    dispatch_request = PluginDispatchRequest(
        plugin_key="plugin",
        contract_version="v1",
        logical_route="EVENT",
        raw_config={"provider_profile": profile.identity},
        raw_state={"step": 1},
        context_state={"step": 1},
        raw_input={"value": 7},
        fact_source=PluginAttemptFactSource(snapshot=snapshot),
        snapshot=snapshot,
    )
    definitions = {
        ("inventory.allowed", "v1"): SystemCapabilityDefinition(
            capability_key="inventory.allowed",
            contract_version="v1",
            mode=SystemCapabilityMode.QUERY,
            input_model=_QueryInput,
            output_model=_QueryOutput,
            handler_factory=_ApprovedInventoryQueryHandler,
            required_ports=(InventoryQueryOperationPort,),
            admission="alternate.v1",
            timeout_seconds=1,
            completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            audit_policy="metadata",
        ),
        ("inventory.blocked", "v1"): SystemCapabilityDefinition(
            capability_key="inventory.blocked",
            contract_version="v1",
            mode=SystemCapabilityMode.QUERY,
            input_model=_QueryInput,
            output_model=_QueryOutput,
            handler_factory=_BlockedInventoryQueryHandler,
            required_ports=(InventoryQueryOperationPort,),
            admission="alternate.v1",
            timeout_seconds=1,
            completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
            audit_policy="metadata",
        ),
    }

    class Dispatcher:
        async def dispatch(self, *, request: object, gateway: object) -> PluginDecision[_PluginState]:
            assert request is dispatch_request
            assert gateway._gateway._admission_profile == profile.identity
            allowed = await gateway.execute("inventory.allowed", "v1", {"value": 7})
            blocked = await gateway.execute("inventory.blocked", "v1", {"value": 7})
            assert isinstance(allowed.outcome, Success)
            assert isinstance(blocked.outcome, Success)
            return PluginDecision(intents=(), next_state=_PluginState(), outcome_code="DONE")

    class Repository:
        async def get_by_id(self, *_args: object) -> object:
            return SimpleNamespace(
                id=91,
                kind="INTERNAL_EVENT",
                event_type="EVENT",
                payload_json={"value": 7},
                trace_id="trace-pinned-profile",
                attempt_count=0,
            )

    class Validation:
        async def pre_gate(self, *_args: object, **_kwargs: object) -> object:
            from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
                ValidationOutcome,
            )

            return ValidationOutcome.continue_orchestrator()

        def classify_estop_or_timer(self, **_kwargs: object) -> object:
            from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
                ValidationOutcome,
            )

            return ValidationOutcome.continue_orchestrator()

    class WriteBack:
        async def commit_plugin_attempt(self, _db: object, **_kwargs: object) -> WriteDisposition:
            return WriteDisposition.COMMITTED

    class Db:
        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

        def in_transaction(self) -> bool:
            return False

    session = SimpleNamespace(
        id=10,
        version=7,
        plugin_state_version=3,
        plugin_state_json={"step": 1},
        plugin_key="plugin",
        contract_version="v1",
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_config_hash="a" * 64,
        plugin_index_digest="b" * 64,
        current_material_unit_id=None,
        status="RUNNING",
        awaiting_device_command_code=None,
        context_json={},
    )

    async def load_related(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return session, SimpleNamespace(id=20), None, None, {}, object(), True

    async def build_dispatch_request(*_args: object, **_kwargs: object) -> PluginDispatchRequest:
        return dispatch_request

    def configure_ports(runtime: object, *, services: object) -> None:
        _ = services
        runtime.port_registry.register(InventoryQueryOperationPort, _InventoryPort)

    monkeypatch.setattr(workline_plugin_binding_service, "get_pinned", AsyncMock(return_value=binding))
    monkeypatch.setattr(capability_index_module, "SYSTEM_CAPABILITY_INDEX", definitions)
    monkeypatch.setattr(module, "_load_related_entities", load_related)
    monkeypatch.setattr(module, "_build_plugin_dispatch_request", build_dispatch_request)
    monkeypatch.setattr(module, "_configure_attempt_runtime_ports", configure_ports)

    bridge = RuntimeInboxProcessorBridge(
        validation_service=Validation(),
        inbox_repository=Repository(),
        writeback_service=WriteBack(),
        plugin_dispatcher=Dispatcher(),
    )
    created_gateways: list[object] = []
    original_create_attempt_runtime = bridge.create_attempt_runtime

    def track_attempt_runtime(*args: object, **kwargs: object) -> object:
        runtime = original_create_attempt_runtime(*args, **kwargs)
        created_gateways.append(runtime.gateway)
        return runtime

    monkeypatch.setattr(bridge, "create_attempt_runtime", track_attempt_runtime)

    result = await bridge.process_claimed(Db(), claim={"id": 91, "processor_token": "lease-production-path"})

    assert result["success"] == 1
    assert len(created_gateways) == 2
    assert all(gateway._closed for gateway in created_gateways)
