"""作业线迁移清单分类、边界校验与确定性摘要测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.runtime.orchestration import repository_wiring
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX_DIGEST
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.models import WorkLine
from src.app.workline.services import (
    WorklineMigrationInventoryInvariantError,
    WorklineMigrationInventoryLimitExceeded,
    WorklineMigrationInventoryService,
    workline_migration_inventory_service,
)

NOW = datetime(2026, 7, 15, 8, 30, tzinfo=UTC)
ZERO_BY_TYPE = {"sessions": 0, "commands": 0, "outboxes": 0, "inboxes": 0, "runtime_holds": 0}


class FakeStatus(str, Enum):
    OPEN = "OPEN"


class FakeRepository:
    def __init__(
        self,
        worklines: list[Any] | None = None,
        summaries: dict[int, dict[str, Any]] | None = None,
        *,
        total: int | None = None,
    ) -> None:
        self.worklines = worklines or []
        self.summaries = summaries or {}
        self.total = len(self.worklines) if total is None else total
        self.get_list_calls: list[dict[str, Any]] = []
        self.summary_calls: list[int] = []

    async def get_list(self, db: Any, **kwargs: Any) -> tuple[int, list[Any]]:
        self.get_list_calls.append(kwargs)
        return self.total, list(self.worklines)

    async def get_unfinished_workload_summary(self, db: Any, workline_id: int) -> dict[str, Any]:
        self.summary_calls.append(workline_id)
        return self.summaries.get(workline_id, _summary())


def _workline(
    workline_id: int,
    line_code: str,
    *,
    active: bool = False,
    plugin: str | None = None,
    version: str | None = None,
    run_mode: Any = "AUTO",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=workline_id,
        line_code=line_code,
        is_active=active,
        plugin_key=plugin,
        contract_version=version,
        run_mode=run_mode,
        active_plugin_binding_id=None,
        active_plugin_binding_version=None,
        active_plugin_config_hash=None,
        active_plugin_index_digest=None,
        active_plugin_provider_requirements_json=[],
    )


def _pin_current(source: SimpleNamespace) -> SimpleNamespace:
    source.active_plugin_binding_id = source.id + 100
    source.active_plugin_binding_version = 1
    source.active_plugin_config_hash = "a" * 64
    source.active_plugin_index_digest = WORKLINE_PLUGIN_INDEX_DIGEST
    return source


def _summary(
    *,
    count: int = 0,
    sample: dict[str, Any] | None = None,
    by_type: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"count": count, "sample": sample, "by_type": dict(ZERO_BY_TYPE if by_type is None else by_type)}


def _definition(key: str = "known", version: str = "current") -> SimpleNamespace:
    return SimpleNamespace(capability_key=key, contract_version=version)


def _profile(
    provider_code: str = "WMS",
    *,
    version: str = "v1",
    environment: str = "sandbox",
) -> ExternalContractProfile:
    return ExternalContractProfile(
        provider_code=provider_code,
        contract_version=version,
        environment=environment,
        inbound_normalizers_event=["SECRET_EVENT"],
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_effect_timeout_seconds=30,
        timeout_retry_retry_backoff_seconds=[1, 2],
        fixture_set_path="tests/fixtures/secret",
        fixture_set_required_cases=["success"],
        security_profile=(
            {"secret_kid": "test-production-kid", "signature_algo": "HS256"} if environment == "production" else {}
        ),
    )


def _service(
    repo: FakeRepository,
    *,
    definitions: list[Any] | None = None,
    profiles: list[Any] | None = None,
    clock: Any = lambda: NOW,
    max_worklines: int = 100,
) -> WorklineMigrationInventoryService:
    return WorklineMigrationInventoryService(
        repository=repo,
        capability_definitions_loader=lambda: definitions if definitions is not None else [_definition()],
        provider_profile_loader=lambda: profiles if profiles is not None else [],
        clock=clock,
        max_worklines=max_worklines,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active", "plugin", "version", "references", "expected_codes", "foundation_ready"),
    [
        (True, "known", "current", 0, ("ACTIVE_PLUGIN_BINDING_INCOMPLETE",), False),
        (
            True,
            None,
            None,
            0,
            ("ACTIVE_WITHOUT_CONTRACT_VERSION", "ACTIVE_WITHOUT_PLUGIN"),
            False,
        ),
        (False, "unknown", "v1", 0, ("UNKNOWN_PLUGIN",), True),
        (True, "unknown", "current", 0, ("UNKNOWN_PLUGIN",), False),
        (True, "known", "old", 0, ("CONTRACT_VERSION_MISMATCH",), False),
        (
            True,
            "known",
            None,
            0,
            ("ACTIVE_WITHOUT_CONTRACT_VERSION", "CONTRACT_VERSION_MISMATCH"),
            False,
        ),
        (False, "known", "current", 1, ("RUNTIME_REFERENCES_PRESENT",), False),
        (False, None, None, 0, (), True),
    ],
)
async def test_inventory_classification_case_table(
    active: bool,
    plugin: str | None,
    version: str | None,
    references: int,
    expected_codes: tuple[str, ...],
    foundation_ready: bool,
) -> None:
    workline = _workline(1, "LINE-01", active=active, plugin=plugin, version=version)
    by_type = {**ZERO_BY_TYPE, "sessions": references}
    repo = FakeRepository([workline], {1: _summary(count=references, by_type=by_type)})

    report = await _service(repo).build_report(object(), environment="production")

    item = report.worklines[0]
    assert tuple(issue.code.value for issue in item.issues) == expected_codes
    assert item.foundation_ready is foundation_ready
    assert report.foundation_ready is foundation_ready
    expected_severity = "WARNING" if not active and expected_codes == ("UNKNOWN_PLUGIN",) else "BLOCKER"
    assert all(issue.severity.value == expected_severity for issue in item.issues)
    assert repo.get_list_calls == [{"limit": 101, "offset": 0, "order_by_raw": [WorkLine.line_code, WorkLine.id]}]


@pytest.mark.asyncio
async def test_workline_fields_are_normalized_before_catalog_lookup_and_classification() -> None:
    source = _workline(
        1,
        " LINE-01 ",
        active=True,
        plugin=" known ",
        version=" current ",
        run_mode=" AUTO ",
    )
    _pin_current(source)

    report = await _service(FakeRepository([source])).build_report(object(), environment="production")

    item = report.worklines[0]
    assert item.line_code == "LINE-01"
    assert item.plugin_key == "known"
    assert item.configured_contract_version == "current"
    assert item.catalog_contract_version == "current"
    assert item.run_mode == "AUTO"
    assert item.issues == ()


@pytest.mark.asyncio
async def test_inventory_digest_includes_binding_index_and_provider_requirements() -> None:
    source = _workline(1, "LINE-01", active=True, plugin="known", version="current")
    source.active_plugin_binding_id = 11
    source.active_plugin_binding_version = 2
    source.active_plugin_config_hash = "a" * 64
    source.active_plugin_index_digest = "b" * 64
    source.active_plugin_provider_requirements_json = ["WMS@v1"]

    first = await _service(FakeRepository([source])).build_report(object(), environment="production")
    source.active_plugin_provider_requirements_json = ["WMS@v1", "ECS@v1"]
    second = await _service(FakeRepository([source])).build_report(object(), environment="production")

    assert first.worklines[0].active_plugin_binding_id == 11
    assert first.worklines[0].provider_requirements == ("WMS@v1",)
    assert first.inventory_digest != second.inventory_digest


@pytest.mark.asyncio
async def test_active_generated_plugin_with_complete_current_binding_is_foundation_ready() -> None:
    source = _workline(1, "LINE-01", active=True, plugin="known", version="current")
    source.active_plugin_binding_id = 11
    source.active_plugin_binding_version = 2
    source.active_plugin_config_hash = "a" * 64
    source.active_plugin_index_digest = WORKLINE_PLUGIN_INDEX_DIGEST

    report = await _service(FakeRepository([source])).build_report(object(), environment="production")

    assert report.worklines[0].issues == ()
    assert report.worklines[0].foundation_ready is True
    assert report.foundation_ready is True


@pytest.mark.asyncio
async def test_active_generated_plugin_with_stale_index_digest_blocks_foundation() -> None:
    source = _workline(1, "LINE-01", active=True, plugin="known", version="current")
    source.active_plugin_binding_id = 11
    source.active_plugin_binding_version = 2
    source.active_plugin_config_hash = "a" * 64
    source.active_plugin_index_digest = ("0" if WORKLINE_PLUGIN_INDEX_DIGEST[0] != "0" else "1") + (
        WORKLINE_PLUGIN_INDEX_DIGEST[1:]
    )

    report = await _service(FakeRepository([source])).build_report(object(), environment="production")

    assert tuple(issue.code.value for issue in report.worklines[0].issues) == ("ACTIVE_PLUGIN_INDEX_DIGEST_MISMATCH",)
    assert report.worklines[0].foundation_ready is False
    assert report.foundation_ready is False


@pytest.mark.asyncio
async def test_inventory_includes_sorted_workitem_and_intent_binding_index_references() -> None:
    class ExtensionRepository:
        async def list_runtime_extension_references_by_workline_ids(
            self, _db: object, workline_ids: tuple[int, ...]
        ) -> dict[int, list[dict[str, object]]]:
            references = [
                {
                    "type": "WORK_ITEM",
                    "reference": "work-item:2",
                    "plugin_key": "known",
                    "plugin_binding_id": 7,
                    "plugin_binding_version": 1,
                    "plugin_config_hash": "a" * 64,
                    "plugin_index_digest": "b" * 64,
                },
                {
                    "type": "INTENT",
                    "reference": "intent:1",
                    "plugin_key": None,
                    "plugin_binding_id": 7,
                    "plugin_binding_version": 1,
                    "plugin_config_hash": "a" * 64,
                    "plugin_index_digest": "b" * 64,
                },
            ]
            return {workline_id: list(references) for workline_id in workline_ids}

    service = WorklineMigrationInventoryService(
        repository=FakeRepository([_workline(1, "LINE-01", plugin="known", version="current")]),
        capability_definitions_loader=lambda: [_definition()],
        provider_profile_loader=list,
        extension_reference_repository=ExtensionRepository(),
        clock=lambda: NOW,
    )

    report = await service.build_report(object(), environment="production")

    assert tuple(reference.type.value for reference in report.worklines[0].runtime_extension_references) == (
        "INTENT",
        "WORK_ITEM",
    )


@pytest.mark.asyncio
async def test_inventory_derives_each_workline_capability_provider_and_port_requirements_from_generated_indexes() -> (
    None
):
    source = _workline(
        1,
        "LINE-01",
        plugin="rough_sorter",
        version="rough_sorter.v2",
    )
    service = WorklineMigrationInventoryService(
        repository=FakeRepository([source]),
        provider_profile_loader=list,
        clock=lambda: NOW,
    )

    report = await service.build_report(object(), environment="production")

    assert report.plugin_index_digest == WORKLINE_PLUGIN_INDEX_DIGEST
    assert report.system_capability_index_digest == SYSTEM_CAPABILITY_INDEX_DIGEST
    requirements = report.worklines[0].capability_requirements
    assert tuple((item.capability_key, item.contract_version) for item in requirements) == (
        ("device.device_command_write", "v1"),
        ("material_flow.material_unit_write", "v1"),
        ("runtime.session_hold", "v1"),
        ("wms.inventory.query_inventory", "v1"),
    )
    query_requirement = requirements[-1]
    assert query_requirement.mode == "QUERY"
    assert query_requirement.admission == "wms.2026-07-28.full-factory"
    assert query_requirement.required_ports == ("src.app.wms_integration.ports.query_execution.WmsQueryExecutionPort",)


@pytest.mark.asyncio
async def test_inventory_loads_extension_references_in_one_batch_for_all_worklines() -> None:
    class ExtensionRepository:
        def __init__(self) -> None:
            self.calls: list[tuple[int, ...]] = []

        async def list_runtime_extension_references_by_workline_ids(
            self,
            _db: object,
            workline_ids: tuple[int, ...],
        ) -> dict[int, list[dict[str, object]]]:
            self.calls.append(workline_ids)
            return {workline_id: [] for workline_id in workline_ids}

    extension_repository = ExtensionRepository()
    service = WorklineMigrationInventoryService(
        repository=FakeRepository(
            [
                _workline(2, "LINE-02", plugin="known", version="current"),
                _workline(1, "LINE-01", plugin="known", version="current"),
            ]
        ),
        capability_definitions_loader=lambda: [_definition()],
        provider_profile_loader=list,
        extension_reference_repository=extension_repository,
        clock=lambda: NOW,
    )

    await service.build_report(object(), environment="production")

    assert extension_repository.calls == [(1, 2)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("line_code", " "),
        ("run_mode", " "),
        ("plugin_key", " "),
        ("contract_version", " "),
    ],
)
async def test_workline_blank_source_fields_fail_closed(field: str, value: str) -> None:
    source = _workline(1, "LINE", plugin="known", version="current")
    setattr(source, field, value)

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(FakeRepository([source])).build_report(object(), environment="production")


@pytest.mark.asyncio
async def test_empty_inventory_produces_ready_deterministic_report() -> None:
    report = await _service(FakeRepository()).build_report(object(), environment="production")

    assert report.worklines == ()
    assert report.issues == ()
    assert report.foundation_ready is True
    assert report.generated_at == NOW
    expected_payload = {
        "schema_version": "workline-migration-inventory-foundation.v1",
        "environment": "production",
        "plugin_index_digest": WORKLINE_PLUGIN_INDEX_DIGEST,
        "system_capability_index_digest": SYSTEM_CAPABILITY_INDEX_DIGEST,
        "foundation_ready": True,
        "worklines": [],
        "provider_profile_catalog": [],
        "issues": [],
    }
    expected_json = json.dumps(expected_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert report.inventory_digest == hashlib.sha256(expected_json.encode()).hexdigest()


@pytest.mark.asyncio
async def test_digest_uses_normalized_report_environment() -> None:
    report_with_spaces = await _service(FakeRepository()).build_report(object(), environment=" prod ")
    report_without_spaces = await _service(FakeRepository()).build_report(object(), environment="prod")

    assert report_with_spaces.environment == report_without_spaces.environment == "prod"
    assert report_with_spaces.inventory_digest == report_without_spaces.inventory_digest
    normalized_payload = report_with_spaces.model_dump(
        mode="json",
        exclude={"generated_at", "inventory_digest"},
    )
    canonical = json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert report_with_spaces.inventory_digest == hashlib.sha256(canonical.encode()).hexdigest()


@pytest.mark.asyncio
async def test_known_plugin_without_version_is_contract_mismatch_even_when_inactive() -> None:
    repo = FakeRepository([_workline(1, "LINE", plugin="known")])

    report = await _service(repo).build_report(object(), environment="production")

    assert [issue.code.value for issue in report.issues] == ["CONTRACT_VERSION_MISMATCH"]


@pytest.mark.asyncio
async def test_capability_catalog_fields_are_normalized_before_lookup() -> None:
    source = _pin_current(_workline(1, "LINE", active=True, plugin="known", version="current"))

    report = await _service(
        FakeRepository([source]),
        definitions=[_definition(" known ", " current ")],
    ).build_report(object(), environment="production")

    item = report.worklines[0]
    assert item.catalog_contract_version == "current"
    assert item.issues == ()


@pytest.mark.asyncio
async def test_capability_catalog_resolves_coexisting_versions_by_exact_identity() -> None:
    worklines = [
        _pin_current(_workline(1, "LINE-V2", active=True, plugin="known", version="v2")),
        _pin_current(_workline(2, "LINE-V3", active=True, plugin="known", version="v3")),
    ]

    report = await _service(
        FakeRepository(worklines),
        definitions=[_definition("known", "v3"), _definition("known", "v2")],
    ).build_report(object(), environment="production")

    assert [
        (item.plugin_key, item.configured_contract_version, item.catalog_contract_version) for item in report.worklines
    ] == [
        ("known", "v2", "v2"),
        ("known", "v3", "v3"),
    ]
    assert report.issues == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["unknown", None])
async def test_capability_catalog_known_key_without_exact_version_fails_closed(version: str | None) -> None:
    source = _workline(1, "LINE", plugin="known", version=version)

    report = await _service(
        FakeRepository([source]),
        definitions=[_definition("known", "v2"), _definition("known", "v3")],
    ).build_report(object(), environment="production")

    item = report.worklines[0]
    assert item.catalog_contract_version is None
    assert [issue.code.value for issue in item.issues] == ["CONTRACT_VERSION_MISMATCH"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "definition",
    [
        _definition(" ", "current"),
        _definition("known", " "),
    ],
)
async def test_capability_catalog_blank_fields_fail_closed(definition: Any) -> None:
    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(FakeRepository(), definitions=[definition]).build_report(object(), environment="production")


@pytest.mark.asyncio
async def test_capability_catalog_rejects_keys_duplicated_after_normalization() -> None:
    definitions = [_definition("known", "current"), _definition(" known ", "current")]

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(FakeRepository(), definitions=definitions).build_report(object(), environment="production")


@pytest.mark.asyncio
@pytest.mark.parametrize(("total", "returned"), [(101, 100), (100, 101)])
async def test_inventory_limit_fails_before_summary_query(total: int, returned: int) -> None:
    repo = FakeRepository([_workline(index, f"L-{index:03}") for index in range(returned)], total=total)

    with pytest.raises(WorklineMigrationInventoryLimitExceeded, match="bulk summary port"):
        await _service(repo).build_report(object(), environment="production")

    assert repo.summary_calls == []


@pytest.mark.asyncio
async def test_inventory_limit_message_uses_injected_limit() -> None:
    repo = FakeRepository([_workline(index, f"L-{index:03}") for index in range(51)], total=51)

    with pytest.raises(WorklineMigrationInventoryLimitExceeded, match="超过 50 条"):
        await _service(repo, max_worklines=50).build_report(object(), environment="production")

    assert repo.summary_calls == []


@pytest.mark.asyncio
async def test_exactly_one_hundred_worklines_are_allowed() -> None:
    worklines = [_workline(index, f"L-{index:03}") for index in range(100)]
    repo = FakeRepository(worklines)

    report = await _service(repo).build_report(object(), environment="production")

    assert len(report.worklines) == 100
    assert len(repo.summary_calls) == 100


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "by_type",
    [
        {"sessions": 0, "commands": 0, "outboxes": 0, "inboxes": 0},
        {**ZERO_BY_TYPE, "other": 0},
        {**ZERO_BY_TYPE, "sessions": -1},
        {**ZERO_BY_TYPE, "sessions": True},
        {**ZERO_BY_TYPE, "sessions": "1"},
    ],
)
async def test_summary_rejects_malformed_by_type(by_type: dict[str, Any]) -> None:
    repo = FakeRepository([_workline(1, "LINE")], {1: _summary(by_type=by_type)})

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(repo).build_report(object(), environment="production")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summary",
    [
        {"count": True, "sample": None, "by_type": ZERO_BY_TYPE},
        {"count": "0", "sample": None, "by_type": ZERO_BY_TYPE},
        {"count": -1, "sample": None, "by_type": ZERO_BY_TYPE},
        {"count": 1, "sample": None, "by_type": ZERO_BY_TYPE},
        {"count": 0, "sample": None, "by_type": ZERO_BY_TYPE, "extra": 1},
        {"count": 0, "by_type": ZERO_BY_TYPE},
    ],
)
async def test_summary_rejects_malformed_top_level(summary: dict[str, Any]) -> None:
    repo = FakeRepository([_workline(1, "LINE")], {1: summary})

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(repo).build_report(object(), environment="production")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_sample", "expected_type", "expected_reference"),
    [
        ({"type": "session", "session_code": "S-1", "status": "RUNNING"}, "SESSION", "S-1"),
        ({"type": "command", "command_code": "C-1", "status": "SENT"}, "COMMAND", "C-1"),
        ({"type": "outbox", "dispatch_key": "D-1", "status": "NEW"}, "OUTBOX", "D-1"),
        ({"type": "inbox", "inbox_id": 42, "status": "FAILED"}, "INBOX", "42"),
        ({"type": "runtime_hold", "count": 3, "status": "ACTIVE_BLOCKING"}, "RUNTIME_HOLD", "count:3"),
    ],
)
async def test_all_repository_samples_are_normalized(
    raw_sample: dict[str, Any], expected_type: str, expected_reference: str
) -> None:
    count_field = {
        "SESSION": "sessions",
        "COMMAND": "commands",
        "OUTBOX": "outboxes",
        "INBOX": "inboxes",
        "RUNTIME_HOLD": "runtime_holds",
    }[expected_type]
    count = raw_sample["count"] if expected_type == "RUNTIME_HOLD" else 1
    by_type = {**ZERO_BY_TYPE, count_field: count}
    repo = FakeRepository([_workline(1, "LINE")], {1: _summary(count=count, sample=raw_sample, by_type=by_type)})

    report = await _service(repo).build_report(object(), environment="production")

    sample = report.worklines[0].runtime_references.sample
    assert sample is not None
    assert sample.type.value == expected_type
    assert sample.reference == expected_reference
    assert not isinstance(sample, dict)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sample",
    [
        {"type": "session", "session_code": "S", "status": "OPEN", "extra": "x"},
        {"type": "session", "command_code": "C", "status": "OPEN"},
        {"type": "session", "session_code": "S", "command_code": "C", "status": "OPEN"},
        {"type": "command", "command_code": "C", "session_code": "S", "status": "OPEN"},
    ],
)
async def test_sample_requires_exact_shape_keys(sample: dict[str, Any]) -> None:
    by_type = {**ZERO_BY_TYPE, "sessions": 1}
    repo = FakeRepository([_workline(1, "LINE")], {1: _summary(count=1, sample=sample, by_type=by_type)})

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(repo).build_report(object(), environment="production")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sample", "nonzero_field"),
    [
        ({"type": "session", "session_code": "S", "status": "OPEN"}, "commands"),
        ({"type": "command", "command_code": "C", "status": "OPEN"}, "outboxes"),
        ({"type": "outbox", "dispatch_key": "D", "status": "OPEN"}, "inboxes"),
        ({"type": "inbox", "inbox_id": 1, "status": "OPEN"}, "runtime_holds"),
        ({"type": "runtime_hold", "count": 1, "status": "OPEN"}, "sessions"),
    ],
)
async def test_sample_type_must_have_positive_corresponding_count(sample: dict[str, Any], nonzero_field: str) -> None:
    by_type = {**ZERO_BY_TYPE, nonzero_field: 1}
    repo = FakeRepository([_workline(1, "LINE")], {1: _summary(count=1, sample=sample, by_type=by_type)})

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(repo).build_report(object(), environment="production")


@pytest.mark.asyncio
async def test_runtime_hold_sample_count_must_equal_summary_count() -> None:
    sample = {"type": "runtime_hold", "count": 2, "status": "ACTIVE_BLOCKING"}
    by_type = {**ZERO_BY_TYPE, "runtime_holds": 3}
    repo = FakeRepository([_workline(1, "LINE")], {1: _summary(count=3, sample=sample, by_type=by_type)})

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(repo).build_report(object(), environment="production")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sample",
    [
        {"type": "unknown", "status": "OPEN", "reference": "x"},
        {"type": "session", "status": "OPEN"},
        {"type": "session", "session_code": None, "status": "OPEN"},
        {"type": "session", "session_code": 1, "status": "OPEN"},
        {"type": "session", "session_code": ["S"], "status": "OPEN"},
        {"type": "command", "command_code": {"code": "C"}, "status": "OPEN"},
        {"type": "outbox", "dispatch_key": object(), "status": "OPEN"},
        {"type": "session", "session_code": " ", "status": "OPEN"},
        {"type": "session", "session_code": "S", "status": " "},
        {"type": "session", "session_code": "S", "status": ["OPEN"]},
        {"type": "session", "session_code": "S", "status": {"value": "OPEN"}},
        {"type": "session", "session_code": "S", "status": object()},
        {"type": "inbox", "inbox_id": 0, "status": "OPEN"},
        {"type": "inbox", "inbox_id": -1, "status": "OPEN"},
        {"type": "inbox", "inbox_id": True, "status": "OPEN"},
        {"type": "inbox", "inbox_id": "1", "status": "OPEN"},
        {"type": "runtime_hold", "count": True, "status": "OPEN"},
        {"type": "runtime_hold", "count": -1, "status": "OPEN"},
        {"type": "runtime_hold", "count": "1", "status": "OPEN"},
    ],
)
async def test_malformed_sample_fails_closed(sample: dict[str, Any]) -> None:
    by_type = {**ZERO_BY_TYPE, "sessions": 1}
    repo = FakeRepository([_workline(1, "LINE")], {1: _summary(count=1, sample=sample, by_type=by_type)})

    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(repo).build_report(object(), environment="production")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_active", ["true", 1])
async def test_inventory_rejects_non_boolean_workline_active_flag(invalid_active: object) -> None:
    source = _workline(1, "LINE")
    source.is_active = invalid_active

    with pytest.raises(WorklineMigrationInventoryInvariantError, match="is_active 必须为 bool"):
        await _service(FakeRepository([source])).build_report(object(), environment="production")


@pytest.mark.asyncio
async def test_inventory_rejects_unhashable_runtime_sample_type() -> None:
    sample = {"type": ["session"], "session_code": "S-1", "status": "OPEN"}
    repo = FakeRepository(
        [_workline(1, "LINE")],
        {1: _summary(count=1, sample=sample, by_type={**ZERO_BY_TYPE, "sessions": 1})},
    )

    with pytest.raises(WorklineMigrationInventoryInvariantError, match=r"summary\.sample\.type 未知"):
        await _service(repo).build_report(object(), environment="production")


@pytest.mark.asyncio
async def test_sample_status_accepts_string_enum_value() -> None:
    by_type = {**ZERO_BY_TYPE, "sessions": 1}
    sample = {"type": "session", "session_code": "S-1", "status": FakeStatus.OPEN}
    repo = FakeRepository([_workline(1, "LINE")], {1: _summary(count=1, sample=sample, by_type=by_type)})

    report = await _service(repo).build_report(object(), environment="production")

    assert report.worklines[0].runtime_references.sample is not None
    assert report.worklines[0].runtime_references.sample.status == "OPEN"


@pytest.mark.asyncio
async def test_zero_total_rejects_sample_but_positive_total_allows_none() -> None:
    zero_repo = FakeRepository(
        [_workline(1, "LINE")],
        {1: _summary(sample={"type": "session", "session_code": "S", "status": "OPEN"})},
    )
    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(zero_repo).build_report(object(), environment="production")

    positive_repo = FakeRepository(
        [_workline(1, "LINE")],
        {1: _summary(count=1, by_type={**ZERO_BY_TYPE, "sessions": 1})},
    )
    report = await _service(positive_repo).build_report(object(), environment="production")
    assert report.worklines[0].runtime_references.sample is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("definitions", "profiles"),
    [
        ([_definition(), _definition()], []),
        ([], [_profile("WMS"), _profile("WMS")]),
    ],
)
async def test_duplicate_catalog_keys_fail_closed(definitions: list[Any], profiles: list[Any]) -> None:
    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(FakeRepository(), definitions=definitions, profiles=profiles).build_report(
            object(), environment="production"
        )


@pytest.mark.asyncio
async def test_provider_profile_is_filtered_to_stable_identity_and_sorted() -> None:
    profile = _profile("Z-WMS")
    report = await _service(FakeRepository(), profiles=[profile, _profile("A-WMS")]).build_report(
        object(), environment="production"
    )

    assert [item.provider_code for item in report.provider_profile_catalog] == ["A-WMS", "Z-WMS"]
    assert set(report.provider_profile_catalog[1].model_dump()) == {
        "provider_code",
        "contract_version",
        "environment",
    }


@pytest.mark.asyncio
async def test_provider_catalog_allows_same_code_across_version_and_environment_and_sorts_by_triple() -> None:
    profiles = [
        _profile("WMS", version="v2", environment="production"),
        _profile("WMS", version="v1", environment="production"),
        _profile("WMS", version="v1", environment="sandbox"),
    ]

    report = await _service(FakeRepository(), profiles=list(reversed(profiles))).build_report(
        object(), environment="production"
    )

    assert [
        (item.provider_code, item.contract_version, item.environment) for item in report.provider_profile_catalog
    ] == [
        ("WMS", "v1", "production"),
        ("WMS", "v1", "sandbox"),
        ("WMS", "v2", "production"),
    ]


@pytest.mark.asyncio
async def test_provider_loader_programming_type_error_propagates_unchanged() -> None:
    expected = TypeError("provider loader bug")

    def broken_loader() -> list[Any]:
        raise expected

    service = WorklineMigrationInventoryService(
        repository=FakeRepository(),
        capability_definitions_loader=lambda: [_definition()],
        provider_profile_loader=broken_loader,
        clock=lambda: NOW,
    )

    with pytest.raises(TypeError) as exc_info:
        await service.build_report(object(), environment="production")

    assert exc_info.value is expected


@pytest.mark.asyncio
async def test_invalid_dynamic_profile_and_workline_fields_become_invariant_errors() -> None:
    invalid_profile = SimpleNamespace(
        provider_code="WMS",
        contract_version="v1",
        environment=" ",
    )
    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(FakeRepository(), profiles=[invalid_profile]).build_report(object(), environment="production")

    minimal_profile = SimpleNamespace(
        provider_code="MINIMAL",
        contract_version="v1",
        environment="sandbox",
    )
    minimal_report = await _service(FakeRepository(), profiles=[minimal_profile]).build_report(
        object(), environment="production"
    )
    assert minimal_report.provider_profile_catalog[0].provider_code == "MINIMAL"

    invalid_workline = _workline(1, " ")
    with pytest.raises(WorklineMigrationInventoryInvariantError):
        await _service(FakeRepository([invalid_workline])).build_report(object(), environment="production")


@pytest.mark.asyncio
async def test_report_sorting_and_digest_ignore_input_order_and_clock() -> None:
    first = _workline(2, "B", active=True, plugin=None, version=None)
    second = _workline(1, "A", plugin="unknown", version="v1")
    summaries = {1: _summary(), 2: _summary()}
    report_a = await _service(
        FakeRepository([first, second], summaries),
        profiles=[_profile("Z-WMS"), _profile("A-WMS")],
        clock=lambda: NOW,
    ).build_report(object(), environment="production")
    report_b = await _service(
        FakeRepository([second, first], summaries),
        profiles=[_profile("A-WMS"), _profile("Z-WMS")],
        clock=lambda: NOW + timedelta(days=1),
    ).build_report(object(), environment="production")

    assert [(item.line_code, item.workline_id) for item in report_a.worklines] == [("A", 1), ("B", 2)]
    assert [issue.code.value for issue in report_a.issues] == [
        "ACTIVE_WITHOUT_CONTRACT_VERSION",
        "ACTIVE_WITHOUT_PLUGIN",
        "UNKNOWN_PLUGIN",
    ]
    assert report_a.inventory_digest == report_b.inventory_digest
    assert report_a.generated_at != report_b.generated_at


@pytest.mark.asyncio
async def test_generated_at_requires_aware_utc_clock() -> None:
    for clock in (
        lambda: datetime(2026, 7, 15, 8, 30),
        lambda: datetime(2026, 7, 15, 16, 30, tzinfo=timezone(timedelta(hours=8))),
    ):
        with pytest.raises(WorklineMigrationInventoryInvariantError):
            await _service(FakeRepository(), clock=clock).build_report(object(), environment="production")


@pytest.mark.asyncio
async def test_final_report_does_not_bypass_validation_with_model_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_model_copy(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("最终报告不得通过 model_copy(update=...) 绕过验证")

    monkeypatch.setattr(
        "src.app.workline.services.migration_inventory_service.WorklineMigrationInventoryReport.model_copy",
        forbidden_model_copy,
    )

    report = await _service(FakeRepository()).build_report(object(), environment="production")

    assert len(report.inventory_digest) == 64


def test_production_singleton_uses_wired_repository_identity() -> None:
    assert workline_migration_inventory_service.repository is repository_wiring.workline_repository


@pytest.mark.asyncio
async def test_service_builds_tuple_compatible_contracts() -> None:
    report = await _service(FakeRepository([_workline(1, "LINE")]), profiles=[_profile()]).build_report(
        object(), environment="production"
    )

    assert isinstance(report.worklines, tuple)
    assert isinstance(report.issues, tuple)
    assert isinstance(report.provider_profile_catalog, tuple)
    assert isinstance(report.worklines[0].issues, tuple)
