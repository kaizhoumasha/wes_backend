"""作业线迁移清单模型合同测试。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.app.workline.models import (
    WorklineMigrationInventoryIssue,
    WorklineMigrationInventoryIssueCode,
    WorklineMigrationInventoryItem,
    WorklineMigrationInventoryReport,
    WorklineMigrationInventorySeverity,
    WorklineProviderProfileInventoryItem,
    WorklineRuntimeReferenceSample,
    WorklineRuntimeReferenceSummary,
    WorklineRuntimeReferenceType,
)


def _valid_report_payload() -> dict[str, object]:
    return {
        "environment": "production",
        "generated_at": datetime(2026, 7, 15, 8, 30, tzinfo=UTC),
        "inventory_digest": "a" * 64,
        "foundation_ready": True,
    }


def _valid_reference_summary_payload() -> dict[str, object]:
    return {
        "sessions": 0,
        "commands": 0,
        "outboxes": 0,
        "inboxes": 0,
        "runtime_holds": 0,
        "total": 0,
    }


def _full_report() -> WorklineMigrationInventoryReport:
    reference_summary = WorklineRuntimeReferenceSummary(
        **_valid_reference_summary_payload(),
        sample=WorklineRuntimeReferenceSample(
            type=WorklineRuntimeReferenceType.SESSION,
            reference="session-1",
            status="RUNNING",
        ),
    )
    issue = WorklineMigrationInventoryIssue(
        code=WorklineMigrationInventoryIssueCode.ACTIVE_WITHOUT_PLUGIN,
        severity=WorklineMigrationInventorySeverity.BLOCKER,
        message="启用作业线未配置插件",
        workline_id=1,
        line_code="LINE-01",
    )
    return WorklineMigrationInventoryReport(
        **_valid_report_payload(),
        worklines=(
            WorklineMigrationInventoryItem(
                workline_id=1,
                line_code="LINE-01",
                is_active=True,
                plugin_key="sorting",
                configured_contract_version="v1",
                catalog_contract_version="v1",
                run_mode="AUTO",
                runtime_references=reference_summary,
                foundation_ready=False,
                issues=(issue,),
            ),
        ),
        provider_profile_catalog=(
            WorklineProviderProfileInventoryItem(
                provider_code="wms-default",
                contract_version="v1",
                environment="production",
            ),
        ),
        issues=(issue,),
    )


def test_minimal_report_locks_schema_defaults_and_empty_collections() -> None:
    report = WorklineMigrationInventoryReport(**_valid_report_payload())

    assert report.schema_version == "workline-migration-inventory-foundation.v1"
    assert report.foundation_ready is True
    assert report.worklines == ()
    assert report.provider_profile_catalog == ()
    assert report.issues == ()


def test_enum_values_are_stable_json_values() -> None:
    issue = WorklineMigrationInventoryIssue(
        code=WorklineMigrationInventoryIssueCode.RUNTIME_REFERENCES_PRESENT,
        severity=WorklineMigrationInventorySeverity.BLOCKER,
        message="仍存在运行态引用",
    )
    sample = WorklineRuntimeReferenceSample(
        type=WorklineRuntimeReferenceType.RUNTIME_HOLD,
        reference="hold-1",
        status="OPEN",
    )

    assert issue.model_dump(mode="json") == {
        "code": "RUNTIME_REFERENCES_PRESENT",
        "severity": "BLOCKER",
        "message": "仍存在运行态引用",
        "workline_id": None,
        "line_code": None,
    }
    assert sample.model_dump(mode="json")["type"] == "RUNTIME_HOLD"


@pytest.mark.parametrize(
    ("model", "payload", "expected_location"),
    [
        (
            WorklineMigrationInventoryIssue,
            {
                "code": "NOT_REGISTERED",
                "severity": WorklineMigrationInventorySeverity.WARNING,
                "message": "未知问题",
            },
            ("code",),
        ),
        (
            WorklineRuntimeReferenceSample,
            {"type": "TASK", "reference": "ref-1", "status": "OPEN"},
            ("type",),
        ),
    ],
)
def test_unknown_enum_values_fail_closed(
    model: type,
    payload: dict[str, object],
    expected_location: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model(**payload)

    assert expected_location in {error["loc"] for error in exc_info.value.errors()}


def test_models_are_strict_frozen_and_reject_unknown_fields() -> None:
    report = WorklineMigrationInventoryReport(**_valid_report_payload())

    with pytest.raises(ValidationError):
        report.foundation_ready = False
    with pytest.raises(ValidationError):
        WorklineMigrationInventoryReport(**_valid_report_payload(), unexpected=True)

    non_strict_payload = _valid_report_payload()
    non_strict_payload["foundation_ready"] = 1
    with pytest.raises(ValidationError):
        WorklineMigrationInventoryReport(**non_strict_payload)


@pytest.mark.parametrize("field", ["sessions", "commands", "outboxes", "inboxes", "runtime_holds", "total"])
@pytest.mark.parametrize("invalid_value", [-1, True, "1"])
def test_reference_counts_are_strict_non_negative_integers(field: str, invalid_value: object) -> None:
    payload = _valid_reference_summary_payload()
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        WorklineRuntimeReferenceSummary(**payload)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("generated_at", datetime(2026, 7, 15, 8, 30)),
        ("generated_at", "not-a-date"),
        ("inventory_digest", "a" * 63),
        ("inventory_digest", "A" * 64),
        ("inventory_digest", "g" * 64),
    ],
)
def test_report_rejects_invalid_timestamp_and_digest(field: str, invalid_value: object) -> None:
    payload = _valid_report_payload()
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        WorklineMigrationInventoryReport(**payload)


def test_aware_utc_datetime_has_iso_json_representation() -> None:
    report = WorklineMigrationInventoryReport(**_valid_report_payload())

    assert report.model_dump(mode="json")["generated_at"] == "2026-07-15T08:30:00Z"


def test_workline_item_exposes_binding_index_and_provider_requirements() -> None:
    item = WorklineMigrationInventoryItem(
        workline_id=1,
        line_code="LINE-01",
        is_active=True,
        plugin_key="rough_sorter",
        configured_contract_version="v1",
        catalog_contract_version="v1",
        run_mode="AUTO",
        active_plugin_binding_id=9,
        active_plugin_binding_version=3,
        active_plugin_config_hash="b" * 64,
        active_plugin_index_digest="c" * 64,
        provider_requirements=("WMS@v1",),
        runtime_references=WorklineRuntimeReferenceSummary(**_valid_reference_summary_payload()),
        foundation_ready=True,
    )

    assert item.active_plugin_binding_id == 9
    assert item.provider_requirements == ("WMS@v1",)


def test_schema_version_is_defaulted_and_cannot_be_overridden() -> None:
    payload = _valid_report_payload()
    payload["schema_version"] = "workline-migration-inventory-foundation.v2"

    with pytest.raises(ValidationError):
        WorklineMigrationInventoryReport(**payload)


def test_report_json_round_trip_preserves_full_contract() -> None:
    report = _full_report()

    restored = WorklineMigrationInventoryReport.model_validate_json(report.model_dump_json())

    assert restored == report
    json_payload = report.model_dump(mode="json")
    assert isinstance(json_payload["worklines"], list)
    assert isinstance(json_payload["worklines"][0]["issues"], list)
    assert isinstance(json_payload["provider_profile_catalog"], list)
    assert isinstance(json_payload["issues"], list)


@pytest.mark.parametrize("through_json_round_trip", [False, True])
def test_all_collection_fields_reject_in_place_mutation(through_json_round_trip: bool) -> None:
    report = _full_report()
    if through_json_round_trip:
        report = WorklineMigrationInventoryReport.model_validate_json(report.model_dump_json())

    workline = report.worklines[0]
    provider = report.provider_profile_catalog[0]

    with pytest.raises(AttributeError):
        report.worklines.append(workline)
    with pytest.raises(AttributeError):
        report.issues.append(report.issues[0])
    with pytest.raises(AttributeError):
        report.provider_profile_catalog.append(provider)
    with pytest.raises(AttributeError):
        workline.issues.append(workline.issues[0])


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (
            WorklineRuntimeReferenceSample,
            {"type": WorklineRuntimeReferenceType.COMMAND, "reference": "ref", "status": "OPEN"},
            "reference",
        ),
        (
            WorklineRuntimeReferenceSample,
            {"type": WorklineRuntimeReferenceType.COMMAND, "reference": "ref", "status": "OPEN"},
            "status",
        ),
        (
            WorklineMigrationInventoryIssue,
            {
                "code": WorklineMigrationInventoryIssueCode.UNKNOWN_PLUGIN,
                "severity": WorklineMigrationInventorySeverity.WARNING,
                "message": "未知插件",
            },
            "message",
        ),
        (
            WorklineMigrationInventoryIssue,
            {
                "code": WorklineMigrationInventoryIssueCode.UNKNOWN_PLUGIN,
                "severity": WorklineMigrationInventorySeverity.WARNING,
                "message": "未知插件",
                "line_code": "LINE-01",
            },
            "line_code",
        ),
        (
            WorklineProviderProfileInventoryItem,
            {
                "provider_code": "wms-default",
                "contract_version": "v1",
                "environment": "production",
            },
            "provider_code",
        ),
    ],
)
def test_string_identity_fields_reject_blank_values(model: type, payload: dict[str, object], field: str) -> None:
    invalid_payload = dict(payload)
    invalid_payload[field] = "   "

    with pytest.raises(ValidationError):
        model(**invalid_payload)


def test_private_base_is_not_exported_from_package_facade() -> None:
    from src.app.workline import models

    assert "_FrozenInventoryModel" not in models.__all__
    assert not hasattr(models, "_FrozenInventoryModel")
