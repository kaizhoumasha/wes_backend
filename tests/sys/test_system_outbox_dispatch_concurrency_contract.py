"""SystemOutbox 调度 identity、lease 与 schema 合同。"""

from __future__ import annotations

import ast
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from src.app.runtime.orchestration.models.dispatch_attempt import WorklineDispatchAttempt
from src.app.sys.dispatch_concurrency import DispatchBucketKey
from src.app.sys.models.outbox import (
    DispatchEnvelope,
    SystemOutbox,
    SystemOutboxCreate,
    SystemOutboxDispatchType,
    SystemOutboxTargetType,
    SystemOutboxUpdate,
)
from src.app.workline.services.write_back_service import _build_external_http_outbox_model


def _create_payload() -> dict[str, object]:
    return {
        "provider_profile_identity": "ecs.device-command.v1",
        "operation_identity": "device.command",
        "dispatch_type": SystemOutboxDispatchType.DEVICE_COMMAND,
        "dispatch_key": "device-command:contract-1",
        "target_type": SystemOutboxTargetType.DEVICE,
        "target_code": "ROBOT-1",
        "payload_json": {"command_code": "contract-1"},
    }


def test_dispatch_envelope_requires_explicit_indexed_scheduling_identity() -> None:
    model_fields = {field.name: field for field in fields(DispatchEnvelope)}

    assert model_fields["provider_profile_identity"].default is MISSING
    assert model_fields["operation_identity"].default is MISSING

    with pytest.raises(TypeError):
        DispatchEnvelope(
            dispatch_key="missing-scheduling-identity",
            dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
            target_type=SystemOutboxTargetType.DEVICE,
            target_code="ROBOT-1",
            payload_json={},
            operation_domain="DEVICE",
        )


def test_production_outbox_construction_authors_scheduling_identity_without_payload_inference() -> None:
    missing: list[str] = []
    for path in Path("src/app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in {"DispatchEnvelope", "SystemOutbox"}:
                continue
            keyword_names = {keyword.arg for keyword in node.keywords if keyword.arg is not None}
            required = {"provider_profile_identity", "operation_identity"}
            if not required.issubset(keyword_names):
                missing.append(f"{path}:{node.lineno}:{node.func.id}")

    assert missing == []


def test_system_outbox_persists_immutable_bucket_and_owner_lease_columns() -> None:
    columns = cast("Any", SystemOutbox).__table__.c
    column_names = set(columns.keys())

    assert {
        "provider_profile_identity",
        "operation_identity",
        "lease_owner_token",
        "lease_expires_at",
    }.issubset(column_names)
    assert columns.provider_profile_identity.nullable is False
    assert columns.operation_identity.nullable is False
    assert columns.lease_owner_token.nullable is True
    assert columns.lease_expires_at.nullable is True

    with pytest.raises(ValidationError):
        SystemOutboxCreate.model_validate(
            {
                key: value
                for key, value in _create_payload().items()
                if key not in {"provider_profile_identity", "operation_identity"}
            }
        )

    assert "provider_profile_identity" not in SystemOutboxUpdate.model_fields
    assert "operation_identity" not in SystemOutboxUpdate.model_fields
    assert "lease_owner_token" not in SystemOutboxUpdate.model_fields
    assert "lease_expires_at" not in SystemOutboxUpdate.model_fields


def test_system_outbox_has_bucket_claim_and_active_lease_indexes() -> None:
    table = cast("Any", SystemOutbox).__table__
    indexes = {index.name: tuple(column.name for column in index.columns) for index in table.indexes}

    assert indexes["ix_system_outbox_dispatch_bucket_claim"] == (
        "provider_profile_identity",
        "operation_identity",
        "status",
        "next_retry_at",
        "created_at",
    )
    assert indexes["ix_system_outbox_active_lease"] == (
        "provider_profile_identity",
        "operation_identity",
        "status",
        "lease_expires_at",
    )
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "ck_system_outbox_ck_system_outbox_dispatch_lease_shape" in constraint_names


def test_dispatch_attempt_mirrors_finite_outbox_lease() -> None:
    table = cast("Any", WorklineDispatchAttempt).__table__

    assert table.c.lease_token.nullable is False
    assert table.c.lease_expires_at.nullable is False
    indexes = {index.name: tuple(column.name for column in index.columns) for index in table.indexes}
    assert indexes["ix_workline_dispatch_attempt_outbox_lease"] == (
        "outbox_id",
        "lease_token",
        "status",
    )
    assert "ck_workline_dispatch_attempts_ck_workline_dispatch_attempt_lease_expiry" in {
        constraint.name for constraint in table.constraints
    }


def test_plugin_external_http_targets_share_one_controlled_bucket_identity() -> None:
    """受控 endpoint code 不得把请求值扩散成高基数限流桶。"""

    ctx = {"session": type("Session", (), {"id": 17, "workline_id": None})()}
    target_codes = (
        "WMS_RCS_RACK_OPERATION",
        "WMS_RCS_BIN_OPERATION",
        "WMS_RCS_FULL_BOX_EXCHANGE",
    )

    outboxes = [
        _build_external_http_outbox_model(
            ctx,  # type: ignore[arg-type]
            dispatch_key=f"plugin-controlled-bucket:{index}",
            target_code=target_code,
            payload_json={"request_id": index},
        )
        for index, target_code in enumerate(target_codes)
    ]

    assert {DispatchBucketKey(outbox.provider_profile_identity, outbox.operation_identity) for outbox in outboxes} == {
        DispatchBucketKey("workline.plugin-runtime.v1.sandbox", "workline.external-http.v1")
    }


def test_plugin_external_http_target_must_exist_in_frozen_endpoint_catalog() -> None:
    """任意插件请求值都不能创建新 bucket，未授权 target 在 author-time fail closed。"""

    ctx = {"session": type("Session", (), {"id": 17, "workline_id": None})()}
    for index in range(100):
        with pytest.raises(ValueError, match="endpoint is not registered"):
            _build_external_http_outbox_model(
                ctx,  # type: ignore[arg-type]
                dispatch_key=f"plugin-unauthorized-target:{index}",
                target_code=f"PLUGIN_FREE_TARGET_{index}",
                payload_json={"request_id": index},
            )


def test_t8e_migration_is_generator_revision_without_business_data_migration() -> None:
    migrations = []
    for path in Path("migrations/versions").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "down_revision" in source and "c325aab03400" in source and "dispatch_concurrency" in path.name:
            migrations.append((path, source))

    assert len(migrations) == 1
    _path, source = migrations[0]
    normalized = source.upper()
    assert "PROVIDER_PROFILE_IDENTITY" in normalized
    assert "OPERATION_IDENTITY" in normalized
    assert "LEASE_OWNER_TOKEN" in normalized
    assert "LEASE_EXPIRES_AT" in normalized
    assert "WORKLINE_DISPATCH_ATTEMPTS" in normalized
    assert 'op.f("ck_system_outbox_ck_system_outbox_dispatch_lease_shape")' in source
    assert 'op.f("ck_workline_dispatch_attempts_ck_workline_dispatch_attempt_lease_expiry")' in source
    assert "OP.EXECUTE" not in normalized
    assert "UPDATE WES_" not in normalized
    assert "INSERT INTO WES_" not in normalized
