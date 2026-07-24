"""Workline 插件 immutable binding 与运行 pin 合同测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Protocol

import pytest
from pydantic import BaseModel, ConfigDict

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.contracts.external_contract_profile_catalog import ExternalContractProfileCatalog
from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition
from src.app.workline.services.plugin_binding_service import (
    PluginBindingAdmissionError,
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)


class InventoryPort(Protocol):
    async def query(self) -> object: ...


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_profile: str
    required_device_codes: tuple[str, ...]


class State(BaseModel):
    phase: str = "READY"


class CapabilityInput(BaseModel):
    business_key: str


class CapabilityOutput(BaseModel):
    accepted: bool


def build_handler(port: InventoryPort) -> object:
    return port


def parse_scan(payload: object) -> object:
    return payload


PLUGIN = WorklinePluginDefinition(
    plugin_key="rough_sorter",
    contract_version="v1",
    config_model=Config,
    state_model=State,
    routes=("SCAN",),
    allowed_capabilities=(("wms.inventory", "v1"),),
    parsers={"SCAN": parse_scan},
)
CAPABILITY = SystemCapabilityDefinition(
    capability_key="wms.inventory",
    contract_version="v1",
    mode=SystemCapabilityMode.QUERY,
    input_model=CapabilityInput,
    output_model=CapabilityOutput,
    handler_factory=build_handler,
    required_ports=(InventoryPort,),
    admission="wms.v1.production",
    timeout_seconds=3,
    completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
    audit_policy="standard",
)


def profile() -> ExternalContractProfile:
    return ExternalContractProfile(
        provider_code="WMS",
        contract_version="v1",
        environment="production",
        timeout_retry_query_timeout_seconds=3,
        timeout_retry_retry_backoff_seconds=[1],
        fixture_set_path="tests/fixtures/external_contracts/wms/v1",
        fixture_set_required_cases=["success"],
        security_profile={"secret_kid": "test-production-kid", "signature_algo": "HS256"},
    )


class FakeRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.rows: dict[int, SimpleNamespace] = {}

    async def next_binding_version(self, _db: object, workline_id: int, plugin_key: str, contract_version: str) -> int:
        return 1 + max((row.binding_version for row in self.rows.values()), default=0)

    async def create_immutable(self, _db: object, data: dict[str, object]) -> SimpleNamespace:
        self.created.append(dict(data))
        row = SimpleNamespace(id=len(self.rows) + 1, **data)
        self.rows[row.id] = row
        return row

    async def get_pinned(self, _db: object, binding_id: int) -> SimpleNamespace | None:
        return self.rows.get(binding_id)


def service(repo: FakeRepository) -> WorklinePluginBindingService:
    return WorklinePluginBindingService(
        repository=repo,
        plugin_index={(PLUGIN.plugin_key, PLUGIN.contract_version): PLUGIN},
        capability_index={(CAPABILITY.capability_key, CAPABILITY.contract_version): CAPABILITY},
        plugin_index_digest="a" * 64,
        profile_catalog=ExternalContractProfileCatalog([profile()]),
        clock=lambda: datetime(2026, 7, 17, 8, tzinfo=UTC),
    )


def test_default_binding_service_wires_runtime_provider_profiles_by_full_identity() -> None:
    resolved = workline_plugin_binding_service.profile_catalog.resolve(
        provider_code="WMS",
        contract_version="2026-07-06.material-flow",
        environment="sandbox",
    )

    assert resolved.identity == "wms.2026-07-06.material-flow.sandbox"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_default_binding_service_exposes_rough_sorter_profile_for_deploy_environment(environment: str) -> None:
    resolved = workline_plugin_binding_service.profile_catalog.resolve(
        provider_code="WMS",
        contract_version="2026-07-06.material-flow",
        environment=environment,
    )

    assert resolved.identity == f"wms.2026-07-06.material-flow.{environment}"


def _rough_sorter_config(*, provider_profile: str = "wms.2026-07-06.material-flow.sandbox") -> dict[str, object]:
    return {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN-01",
        "pipeline_output_location": "PIPELINE-OUT-01",
        "ng_location": "NG-01",
        "warehouse_code": "WH-01",
        "owner_code": "OWNER-01",
        "provider_profile": provider_profile,
    }


def _rough_sorter_devices() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=1,
            device_code="RS-IN-01",
            device_role="ROUGH_SORTER_INPUT_ARM",
            work_line_id=17,
            vendor_type="ECS",
        ),
        SimpleNamespace(
            id=2,
            device_code="RS-CONVEYOR-01",
            device_role="ROUGH_SORTER_CONVEYOR",
            work_line_id=17,
            vendor_type="ECS",
        ),
        SimpleNamespace(
            id=3,
            device_code="RS-OUT-01",
            device_role="ROUGH_SORTER_OUTPUT_ARM",
            work_line_id=17,
            vendor_type="ECS",
        ),
    ]


def _real_rough_sorter_service(
    repo: FakeRepository,
    *,
    profile_catalog: ExternalContractProfileCatalog | None = None,
) -> WorklinePluginBindingService:
    return WorklinePluginBindingService(
        repository=repo,
        profile_catalog=profile_catalog or workline_plugin_binding_service.profile_catalog,
        clock=lambda: datetime(2026, 7, 17, 8, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_real_rough_sorter_activation_snapshots_exact_profile_and_required_port() -> None:
    repo = FakeRepository()
    workline = SimpleNamespace(
        id=17,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        config=_rough_sorter_config(),
        version=4,
    )

    binding = await _real_rough_sorter_service(repo).activate(
        object(),
        workline=workline,
        expected_workline_version=4,
        actor="operator",
        reason="dev-cutover",
        environment="sandbox",
        devices=_rough_sorter_devices(),
    )

    assert [profile["provider_code"] for profile in binding.provider_profile_snapshot_json] == ["WMS"]
    assert binding.provider_profile_snapshot_json[0]["contract_version"] == "2026-07-06.material-flow"
    assert binding.provider_profile_snapshot_json[0]["environment"] == "sandbox"


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["staging", "production"])
async def test_real_rough_sorter_deploy_activation_pins_configured_role_devices(environment: str) -> None:
    repo = FakeRepository()
    workline = SimpleNamespace(
        id=17,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        config=_rough_sorter_config(provider_profile=f"wms.2026-07-06.material-flow.{environment}"),
        version=4,
    )

    binding = await _real_rough_sorter_service(repo).activate(
        object(),
        workline=workline,
        expected_workline_version=4,
        actor="operator",
        reason=f"{environment}-cutover",
        environment=environment,
        devices=_rough_sorter_devices(),
    )

    assert binding.provider_profile_snapshot_json[0]["environment"] == environment
    assert binding.device_snapshot_json == [
        {
            "device_id": 2,
            "device_code": "RS-CONVEYOR-01",
            "device_role": "ROUGH_SORTER_CONVEYOR",
            "workline_id": 17,
            "provider_code": "ECS",
        },
        {
            "device_id": 1,
            "device_code": "RS-IN-01",
            "device_role": "ROUGH_SORTER_INPUT_ARM",
            "workline_id": 17,
            "provider_code": "ECS",
        },
        {
            "device_id": 3,
            "device_code": "RS-OUT-01",
            "device_role": "ROUGH_SORTER_OUTPUT_ARM",
            "workline_id": 17,
            "provider_code": "ECS",
        },
    ]


@pytest.mark.parametrize(
    "current_devices",
    [
        [
            SimpleNamespace(
                id=9,
                device_code="RS-IN-REPLACEMENT",
                device_role="ROUGH_SORTER_INPUT_ARM",
                work_line_id=17,
                vendor_type="ECS",
            )
        ],
        [
            SimpleNamespace(
                id=1,
                device_code="RS-IN-01",
                device_role="ROUGH_SORTER_CONVEYOR",
                work_line_id=17,
                vendor_type="ECS",
            )
        ],
        [
            SimpleNamespace(
                id=1,
                device_code="RS-IN-RENAMED",
                device_role="ROUGH_SORTER_INPUT_ARM",
                work_line_id=17,
                vendor_type="ECS",
            )
        ],
        [
            SimpleNamespace(
                id=1,
                device_code="RS-IN-01",
                device_role="ROUGH_SORTER_INPUT_ARM",
                work_line_id=17,
                vendor_type="ECS",
            ),
            SimpleNamespace(
                id=9,
                device_code="RS-IN-02",
                device_role="ROUGH_SORTER_INPUT_ARM",
                work_line_id=17,
                vendor_type="ECS",
            ),
        ],
        [
            SimpleNamespace(
                id=1,
                device_code="RS-IN-01",
                device_role="ROUGH_SORTER_INPUT_ARM",
                work_line_id=17,
                vendor_type="OTHER",
            )
        ],
    ],
)
def test_execution_rejects_device_topology_drift_from_immutable_binding(
    current_devices: list[SimpleNamespace],
) -> None:
    binding = SimpleNamespace(
        workline_id=17,
        typed_config_json={"device_roles": {"input_arm": "ROUGH_SORTER_INPUT_ARM"}},
        device_snapshot_json=[
            {
                "device_id": 1,
                "device_code": "RS-IN-01",
                "device_role": "ROUGH_SORTER_INPUT_ARM",
                "workline_id": 17,
                "provider_code": "ECS",
            }
        ],
    )
    validator = getattr(WorklinePluginBindingService, "assert_device_snapshot", lambda *_args, **_kwargs: None)

    with pytest.raises(PluginBindingAdmissionError, match=r"device (snapshot|role requirement)"):
        validator(binding, devices_by_role={"ROUGH_SORTER_INPUT_ARM": current_devices})


@pytest.mark.asyncio
async def test_real_rough_sorter_activation_rejects_profile_from_other_environment() -> None:
    workline = SimpleNamespace(
        id=17,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        config=_rough_sorter_config(provider_profile="wms.2026-07-06.material-flow.sandbox"),
        version=4,
    )

    with pytest.raises(PluginBindingAdmissionError, match="environment"):
        await _real_rough_sorter_service(FakeRepository()).activate(
            object(),
            workline=workline,
            expected_workline_version=4,
            actor="operator",
            reason="production-cutover",
            environment="production",
            devices=_rough_sorter_devices(),
        )


@pytest.mark.asyncio
async def test_real_rough_sorter_activation_rejects_profile_mismatch() -> None:
    workline = SimpleNamespace(
        id=17,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        config=_rough_sorter_config(provider_profile="wms.other.sandbox"),
        version=4,
    )

    with pytest.raises(PluginBindingAdmissionError, match="provider"):
        await _real_rough_sorter_service(FakeRepository()).activate(
            object(),
            workline=workline,
            expected_workline_version=4,
            actor="operator",
            reason="dev-cutover",
            environment="sandbox",
            devices=[],
        )


@pytest.mark.asyncio
async def test_real_rough_sorter_activation_rejects_undeclared_profile() -> None:
    workline = SimpleNamespace(
        id=17,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        config=_rough_sorter_config(),
        version=4,
    )

    with pytest.raises(PluginBindingAdmissionError, match="provider"):
        await _real_rough_sorter_service(FakeRepository(), profile_catalog=ExternalContractProfileCatalog(())).activate(
            object(),
            workline=workline,
            expected_workline_version=4,
            actor="operator",
            reason="dev-cutover",
            environment="sandbox",
            devices=[],
        )


@pytest.mark.asyncio
async def test_activation_creates_new_immutable_version_with_canonical_config_and_snapshots() -> None:
    repo = FakeRepository()
    binding_service = service(repo)
    workline = SimpleNamespace(
        id=7,
        plugin_key="rough_sorter",
        contract_version="v1",
        config={"required_device_codes": ["PLC-01"], "provider_profile": "wms.v1.production"},
        version=4,
    )
    devices = [SimpleNamespace(device_code="PLC-01", provider_code="ECS")]

    first = await binding_service.activate(
        object(),
        workline=workline,
        expected_workline_version=4,
        actor="operator-1",
        reason="go-live",
        environment="production",
        devices=devices,
    )
    workline.config = {"provider_profile": "wms.v1.production", "required_device_codes": ("PLC-01",)}
    second = await binding_service.activate(
        object(),
        workline=workline,
        expected_workline_version=4,
        actor="operator-2",
        reason="re-approve",
        environment="production",
        devices=devices,
    )

    assert (first.binding_version, second.binding_version) == (1, 2)
    assert first.typed_config_hash == second.typed_config_hash
    assert first.generated_index_digest == "a" * 64
    assert first.provider_profile_snapshot_json[0]["provider_code"] == "WMS"
    assert first.device_snapshot_json == [{"device_code": "PLC-01", "provider_code": "ECS"}]
    assert first.activated_by == "operator-1"
    assert first.activated_reason == "go-live"
    assert not hasattr(workline, "active_plugin_binding_id")
    assert first.provider_profile_snapshot_json[0]["environment"] == "production"
    assert repo.rows[1].binding_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "devices", "message"),
    [
        ({"provider_profile": "wms.v1.production", "required_device_codes": ["MISSING"]}, [], "device"),
        ({"provider_profile": "wms.unknown.production", "required_device_codes": []}, [], "provider"),
        ({"provider_profile": "wms.v1.production", "required_device_codes": [], "extra": True}, [], "config"),
    ],
)
async def test_activation_fails_closed_for_invalid_config_device_provider_or_port(
    config: dict[str, object], devices: list[object], message: str
) -> None:
    workline = SimpleNamespace(id=7, plugin_key="rough_sorter", contract_version="v1", config=config, version=4)

    with pytest.raises(PluginBindingAdmissionError, match=message):
        await service(FakeRepository()).activate(
            object(),
            workline=workline,
            expected_workline_version=4,
            actor="operator",
            reason="activate",
            environment="production",
            devices=devices,
        )


@pytest.mark.asyncio
async def test_pinned_retry_reads_disabled_row_but_rechecks_runtime_admission() -> None:
    repo = FakeRepository()
    binding_service = service(repo)
    workline = SimpleNamespace(
        id=7,
        plugin_key="rough_sorter",
        contract_version="v1",
        config={"provider_profile": "wms.v1.production", "required_device_codes": []},
        version=4,
    )
    row = await binding_service.activate(
        object(),
        workline=workline,
        expected_workline_version=4,
        actor="operator",
        reason="activate",
        environment="production",
        devices=[],
    )
    row.is_enabled = False
    row.disabled_reason = "kill-switch"

    assert await binding_service.get_pinned(object(), binding_id=row.id) is row
    with pytest.raises(PluginBindingAdmissionError, match="kill switch"):
        await binding_service.assert_execution_admitted(
            row, environment="production", now=datetime(2026, 7, 17, 9, tzinfo=UTC)
        )


def test_revoked_binding_is_rejected_even_when_kill_switch_remains_enabled() -> None:
    binding = SimpleNamespace(
        is_enabled=True,
        is_revoked=True,
        environment="production",
        valid_from=None,
        valid_until=None,
    )

    with pytest.raises(PluginBindingAdmissionError, match="撤权"):
        WorklinePluginBindingService.assert_execution_admitted(
            binding,
            environment="production",
            now=datetime(2026, 7, 17, 9, tzinfo=UTC),
        )


def test_pin_runtime_records_copies_one_binding_identity_and_json_state() -> None:
    binding = SimpleNamespace(
        id=8,
        plugin_key="rough_sorter",
        binding_version=2,
        typed_config_hash="a" * 64,
        generated_index_digest="b" * 64,
    )
    records = [SimpleNamespace(), SimpleNamespace(), SimpleNamespace()]

    WorklinePluginBindingService.pin_runtime_records(
        binding=binding,
        records=records,
        plugin_state=State(phase="RUNNING"),
    )

    for record in records:
        assert record.plugin_binding_id == 8
        assert record.plugin_binding_version == 2
        assert record.plugin_config_hash == "a" * 64
        assert record.plugin_index_digest == "b" * 64
        assert record.plugin_state_json == {"phase": "RUNNING"}
        assert record.plugin_state_version == 0


def test_runtime_models_pin_same_binding_identity_and_json_state() -> None:
    from src.app.runtime.orchestration.execution_session import ExecutionSession
    from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
    from src.app.runtime.orchestration.models.session import WorklineSessionBase

    for model in (WorklineSessionBase, ExecutionSession, ExecutionWorkItem):
        fields = model.model_fields
        assert {"plugin_binding_id", "plugin_binding_version", "plugin_config_hash", "plugin_index_digest"} <= set(
            fields
        )
    assert "plugin_state_json" in WorklineSessionBase.model_fields
    assert "plugin_state_json" in ExecutionSession.model_fields


@pytest.mark.parametrize(("plugin_key", "contract_version"), [(None, "v1"), ("rough_sorter", None)])
def test_binding_activation_rejects_incomplete_plugin_identity(plugin_key: object, contract_version: object) -> None:
    workline = SimpleNamespace(id=7, plugin_key=plugin_key, contract_version=contract_version, config={})

    with pytest.raises(PluginBindingAdmissionError, match="plugin identity 缺失"):
        service(FakeRepository()).validate_activation_configuration(
            workline=workline,
            environment="production",
            devices=[],
        )


@pytest.mark.asyncio
async def test_missing_pinned_binding_is_rejected() -> None:
    with pytest.raises(PluginBindingAdmissionError, match="pinned binding 不存在: 9"):
        await service(FakeRepository()).get_pinned(object(), binding_id=9)
