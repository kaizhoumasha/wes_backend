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
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    RuntimeInboxWriteBackResult,
    RuntimeInboxWriteBackService,
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
from src.app.wms_integration.ports.effect_preparation import WmsEffectPreparationPort
from src.app.wms_integration.ports.inventory_operations import InventorySnapshotQueryRequest
from src.app.wms_integration.ports.query_execution import WmsQueryExecutionPort
from src.app.wms_integration.ports.query_outcome import QueryContractFailure, QuerySuccess, QueryTechnicalFailure
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


class _EffectPreparationPort:
    async def prepare(self, *_args: object, **_kwargs: object) -> object:
        return object()


def _pre_attempt_status(value: object) -> str | None:
    status = getattr(value, "status", None)
    return getattr(status, "value", status)


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
    registry.register(WmsQueryExecutionPort, _InventoryPort)
    runtime = RuntimeInboxProcessorBridge().create_attempt_runtime(
        "lease-method-allowlist",
        base_registry=registry,
        definitions={},
        provider_profile=_profile(),
    )
    inventory_port = runtime.context.get_query_port(WmsQueryExecutionPort)

    assert callable(inventory_port.execute)
    assert callable(inventory_port.undeclared_query)


def test_attempt_runtime_registers_shared_data_lane_query_port() -> None:
    registry = CapabilityPortRegistry()
    runtime = SimpleNamespace(port_registry=registry)

    _configure_attempt_runtime_ports(
        runtime,
        services=SimpleNamespace(
            wms_query_execution_port=_InventoryPort(),
        ),
    )

    assert registry.get(WmsQueryExecutionPort) is registry.get(WmsQueryExecutionPort)


def test_attempt_runtime_registers_effect_port_without_query_port() -> None:
    registry = CapabilityPortRegistry()
    runtime = SimpleNamespace(port_registry=registry)
    effect_port = _EffectPreparationPort()

    _configure_attempt_runtime_ports(
        runtime,
        services=SimpleNamespace(
            wms_query_execution_port=None,
            wms_effect_preparation_port=effect_port,
        ),
    )

    assert not registry.is_registered(WmsQueryExecutionPort)
    assert registry.get(WmsEffectPreparationPort) is effect_port


def test_attempt_runtime_registers_query_and_effect_ports_independently() -> None:
    registry = CapabilityPortRegistry()
    runtime = SimpleNamespace(port_registry=registry)
    query_port = _InventoryPort()
    effect_port = _EffectPreparationPort()

    _configure_attempt_runtime_ports(
        runtime,
        services=SimpleNamespace(
            wms_query_execution_port=query_port,
            wms_effect_preparation_port=effect_port,
        ),
    )

    assert registry.get(WmsQueryExecutionPort) is query_port
    assert registry.get(WmsEffectPreparationPort) is effect_port


def test_stage_three_effect_applier_resolves_attempt_scoped_effect_port() -> None:
    registry = CapabilityPortRegistry()
    runtime = SimpleNamespace(port_registry=registry)
    effect_port = _EffectPreparationPort()
    _configure_attempt_runtime_ports(
        runtime,
        services=SimpleNamespace(wms_query_execution_port=None, wms_effect_preparation_port=effect_port),
    )

    effect_applier = RuntimeInboxWriteBackService().effect_applier_for_attempt(
        effect_port_resolver=registry.get,
    )

    assert (
        effect_applier._system_capability_effect_service._resolve_effect_port(WmsEffectPreparationPort) is effect_port
    )


def test_stage_three_effect_applier_injects_frozen_admission_policy() -> None:
    def policy(_definition) -> bool:
        return False

    effect_applier = RuntimeInboxWriteBackService().effect_applier_for_attempt(
        effect_port_resolver=lambda _port_type: object(),
        allow_new_claim=policy,
    )

    intent_service = effect_applier._system_capability_effect_service._intent_service
    assert intent_service._allow_new_claim is policy


def test_runtime_services_expose_the_bound_effect_runtime_policy() -> None:
    def policy(_definition) -> bool:
        return False

    effect_port = SimpleNamespace(allow_new_claim=policy)

    services = build_workline_runtime_services(
        wms_effect_preparation_port=effect_port,
    )

    assert services.allow_new_system_capability_claim is policy


def test_runtime_services_reject_effect_port_without_admission_policy() -> None:
    with pytest.raises(RuntimeError, match="allow_new_claim"):
        build_workline_runtime_services(
            wms_effect_preparation_port=object(),
        )


def test_stage_three_rejects_policy_when_custom_effect_applier_would_ignore_it() -> None:
    service = RuntimeInboxWriteBackService(effect_applier=object())

    with pytest.raises(RuntimeError, match="custom effect applier"):
        service.effect_applier_for_attempt(
            effect_port_resolver=lambda _port_type: object(),
            allow_new_claim=lambda _definition: False,
        )


def test_stage_three_rejects_policy_without_effect_port_resolver() -> None:
    with pytest.raises(RuntimeError, match="effect port resolver"):
        RuntimeInboxWriteBackService().effect_applier_for_attempt(
            allow_new_claim=lambda _definition: False,
        )


@pytest.mark.asyncio
async def test_real_runtime_services_leave_query_port_unregistered_without_lane_owner() -> None:
    runtime = SimpleNamespace(port_registry=CapabilityPortRegistry())
    services = build_workline_runtime_services(
        db=object(),
        session=SimpleNamespace(run_mode="NORMAL"),
    )
    _configure_attempt_runtime_ports(runtime, services=services)
    assert not runtime.port_registry.is_registered(WmsQueryExecutionPort)


@pytest.mark.asyncio
async def test_real_runtime_services_leave_effect_port_unregistered_without_owner() -> None:
    runtime = SimpleNamespace(port_registry=CapabilityPortRegistry())
    services = build_workline_runtime_services(
        db=object(),
        session=SimpleNamespace(run_mode="NORMAL"),
    )

    _configure_attempt_runtime_ports(runtime, services=services)

    assert not runtime.port_registry.is_registered(WmsEffectPreparationPort)


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
    registry.register(WmsQueryExecutionPort, _InventoryPort)
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
    inventory_port = runtime.context.get_query_port(WmsQueryExecutionPort)
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
            required_ports=(WmsQueryExecutionPort,),
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
            required_ports=(WmsQueryExecutionPort,),
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
        async def commit_plugin_attempt(self, _db: object, **_kwargs: object) -> RuntimeInboxWriteBackResult:
            return RuntimeInboxWriteBackResult(disposition=WriteDisposition.COMMITTED)

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
        runtime.port_registry.register(WmsQueryExecutionPort, _InventoryPort)

    q19_hook_calls = 0

    async def resolve_q19_before_plugin(*_args: object, **_kwargs: object) -> object:
        from src.app.runtime.workline_plugins.pre_attempt import PreAttemptResolution

        nonlocal q19_hook_calls
        q19_hook_calls += 1
        return PreAttemptResolution.not_applicable()

    monkeypatch.setattr(workline_plugin_binding_service, "get_pinned", AsyncMock(return_value=binding))
    monkeypatch.setattr(capability_index_module, "SYSTEM_CAPABILITY_INDEX", definitions)
    monkeypatch.setattr(module, "_load_related_entities", load_related)
    monkeypatch.setattr(module, "_build_plugin_dispatch_request", build_dispatch_request)
    monkeypatch.setattr(module, "_configure_attempt_runtime_ports", configure_ports)
    monkeypatch.setattr(module, "resolve_plugin_pre_attempt_facts", resolve_q19_before_plugin)

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
    assert q19_hook_calls == 1
    assert len(created_gateways) == 2
    assert all(gateway._closed for gateway in created_gateways)


@pytest.mark.asyncio
async def test_production_q19_hook_builds_request_from_scan_and_measurement_before_pick_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.workline_plugins.rough_sorter import pre_attempt as module

    snapshot = PinnedPluginSnapshot(
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        binding_identity="binding:17:4",
        binding_id=17,
        binding_version=4,
        config_hash="a" * 64,
        index_digest="b" * 64,
        profile_identity="wms.2026-07-28.full-factory",
    )
    dispatch_request = PluginDispatchRequest(
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        logical_route="SCAN_COMPLETED",
        raw_config={"provider_profile": snapshot.profile_identity},
        raw_state={"phase": "READY"},
        context_state={"phase": "READY"},
        raw_input={
            "data": {
                "HHPN": "HHPN-Q19",
                "MfrPN": "MFR-Q19",
                "Qty": "2",
                "DateCode": "20260729",
                "LotCode": "LOT-Q19",
                "PkgID": "PKG-Q19",
            },
            "reel_diameter_mm": "100.5",
            "reel_thickness_mm": "10.25",
        },
        fact_source=PluginAttemptFactSource(
            snapshot=snapshot,
            raw_input={},
        ),
        snapshot=snapshot,
    )
    q19_service = SimpleNamespace(resolve=AsyncMock(return_value=QuerySuccess(object())))
    session = SimpleNamespace(id=19, session_code="SESSION-Q19", barcode="RAW-SESSION-Q19")
    workline = SimpleNamespace(id=29, line_code="ROUGH-SORTER-Q19")

    resolved = await module.resolve_pre_attempt_facts(
        object(),
        session=session,
        workline=workline,
        dispatch_request=dispatch_request,
        services=SimpleNamespace(rough_sorter_q19_admission_service=q19_service),
    )

    assert _pre_attempt_status(resolved) == "FACTS_CHANGED"
    request = q19_service.resolve.await_args.kwargs["request"]
    assert q19_service.resolve.await_args.kwargs["session_id"] == 19
    assert request.raw_code == "RAW-SESSION-Q19"
    assert request.six_in_one.PkgID == "PKG-Q19"
    assert str(request.reel_diameter_mm) == "100.5"
    assert str(request.reel_thickness_mm) == "10.25"
    assert request.station_code == "ROUGH-SORTER-Q19"
    assert request.correlation_id == "workline-session:SESSION-Q19"

    late_command = dispatch_request.model_copy(
        update={
            "logical_route": "COMMAND_RESULT",
            "raw_input": {
                "route": "COMMAND_RESULT",
                "command_code": "CMD-Q19-1",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "data": {"reel_diameter": "100.5", "reel_thickness": "10.25"},
                "error_detail": {},
            },
        }
    )
    assert (
        _pre_attempt_status(
            await module.resolve_pre_attempt_facts(
                object(),
                session=session,
                workline=workline,
                dispatch_request=late_command,
                services=SimpleNamespace(rough_sorter_q19_admission_service=q19_service),
            )
        )
        == "NOT_APPLICABLE"
    )
    invalid_six_in_one = dispatch_request.model_copy(
        update={
            "raw_input": {
                **dispatch_request.raw_input,
                "data": {key: value for key, value in dispatch_request.raw_input["data"].items() if key != "Qty"},
            }
        }
    )
    invalid_resolution = await module.resolve_pre_attempt_facts(
        object(),
        session=session,
        workline=workline,
        dispatch_request=invalid_six_in_one,
        services=SimpleNamespace(rough_sorter_q19_admission_service=q19_service),
    )
    assert _pre_attempt_status(invalid_resolution) == "BLOCKED"
    assert getattr(invalid_resolution, "reason_code", None) == "WMS_Q19_REQUEST_INVALID"

    from src.app.runtime.capabilities.material_flow import rough_sorter_q19_admission_service as service_module

    fallback_service = SimpleNamespace(resolve=AsyncMock(return_value=QuerySuccess(object())))
    monkeypatch.setattr(service_module, "RoughSorterQ19AdmissionService", lambda _runtime: fallback_service)
    assert (
        _pre_attempt_status(
            await module.resolve_pre_attempt_facts(
                object(),
                session=session,
                workline=workline,
                dispatch_request=dispatch_request,
                services=SimpleNamespace(wms_query_execution_port=SimpleNamespace(project=lambda request: request)),
            )
        )
        == "FACTS_CHANGED"
    )
    assert q19_service.resolve.await_count == 1

    for failure in (
        QueryTechnicalFailure("WMS_PROVIDER_TIMEOUT", "timeout", retryable=True),
        QueryContractFailure("WMS_MALFORMED_RESPONSE", "invalid"),
    ):
        q19_service.resolve.return_value = failure
        blocked = await module.resolve_pre_attempt_facts(
            object(),
            session=session,
            workline=workline,
            dispatch_request=dispatch_request,
            services=SimpleNamespace(rough_sorter_q19_admission_service=q19_service),
        )
        assert _pre_attempt_status(blocked) == "BLOCKED"
        assert getattr(blocked, "reason_code", None) == failure.reason_code

    missing_runtime = await module.resolve_pre_attempt_facts(
        object(),
        session=session,
        workline=workline,
        dispatch_request=dispatch_request,
        services=SimpleNamespace(),
    )
    assert _pre_attempt_status(missing_runtime) == "BLOCKED"
    assert getattr(missing_runtime, "reason_code", None) == "WMS_Q19_RUNTIME_UNAVAILABLE"
    missing_measurement = dispatch_request.model_copy(
        update={
            "raw_input": {
                **dispatch_request.raw_input,
                "reel_thickness_mm": None,
            }
        }
    )
    missing_facts = await module.resolve_pre_attempt_facts(
        object(),
        session=session,
        workline=workline,
        dispatch_request=missing_measurement,
        services=SimpleNamespace(rough_sorter_q19_admission_service=q19_service),
    )
    assert _pre_attempt_status(missing_facts) == "BLOCKED"
    assert getattr(missing_facts, "reason_code", None) == "WMS_Q19_REQUEST_FACTS_MISSING"


@pytest.mark.asyncio
async def test_generic_pre_attempt_facade_is_generated_identity_scoped_and_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.workline_plugins import pre_attempt as module

    common = {
        "db": object(),
        "session": object(),
        "workline": object(),
        "services": object(),
    }
    assert (
        _pre_attempt_status(
            await module.resolve_plugin_pre_attempt_facts(
                **common,
                dispatch_request=SimpleNamespace(plugin_key=None, contract_version="v1"),
            )
        )
        == "NOT_APPLICABLE"
    )
    assert (
        _pre_attempt_status(
            await module.resolve_plugin_pre_attempt_facts(
                **common,
                dispatch_request=SimpleNamespace(plugin_key="rough_sorter", contract_version=None),
            )
        )
        == "NOT_APPLICABLE"
    )
    assert (
        _pre_attempt_status(
            await module.resolve_plugin_pre_attempt_facts(
                **common,
                dispatch_request=SimpleNamespace(plugin_key="unknown", contract_version="v1"),
            )
        )
        == "NOT_APPLICABLE"
    )
    assert (
        _pre_attempt_status(
            await module.resolve_plugin_pre_attempt_facts(
                **common,
                dispatch_request=SimpleNamespace(
                    plugin_key="smt_sorting_inbound",
                    contract_version="smt_sorting_inbound.v1",
                ),
            )
        )
        == "NOT_APPLICABLE"
    )

    monkeypatch.setattr(module, "import_module", lambda _name: SimpleNamespace(resolve_pre_attempt_facts=None))
    rough_request = SimpleNamespace(plugin_key="rough_sorter", contract_version="rough_sorter.v2")
    assert (
        _pre_attempt_status(
            await module.resolve_plugin_pre_attempt_facts(
                **common,
                dispatch_request=rough_request,
            )
        )
        == "NOT_APPLICABLE"
    )

    async def resolver(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(module, "import_module", lambda _name: SimpleNamespace(resolve_pre_attempt_facts=resolver))
    invalid_result = await module.resolve_plugin_pre_attempt_facts(
        **common,
        dispatch_request=rough_request,
    )
    assert _pre_attempt_status(invalid_result) == "BLOCKED"
    assert getattr(invalid_result, "reason_code", None) == "PLUGIN_PRE_ATTEMPT_RESULT_INVALID"

    expected_resolution = module.PreAttemptResolution.facts_changed()

    async def typed_resolver(*_args: object, **_kwargs: object) -> module.PreAttemptResolution:
        return expected_resolution

    monkeypatch.setattr(
        module, "import_module", lambda _name: SimpleNamespace(resolve_pre_attempt_facts=typed_resolver)
    )
    assert (
        await module.resolve_plugin_pre_attempt_facts(
            **common,
            dispatch_request=rough_request,
        )
        is expected_resolution
    )

    with pytest.raises(ValueError, match="only BLOCKED"):
        module.PreAttemptResolution(module.PreAttemptStatus.BLOCKED)
    with pytest.raises(ValueError, match="only BLOCKED"):
        module.PreAttemptResolution(module.PreAttemptStatus.NOT_APPLICABLE, reason_code="UNEXPECTED")

    def broken_import(_name: str) -> object:
        exc = ModuleNotFoundError("nested dependency missing")
        exc.name = "nested_dependency"
        raise exc

    monkeypatch.setattr(module, "import_module", broken_import)
    with pytest.raises(ModuleNotFoundError, match="nested dependency missing"):
        await module.resolve_plugin_pre_attempt_facts(
            **common,
            dispatch_request=rough_request,
        )
