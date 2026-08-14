"""RuntimeHold 与 NG reason 不再暴露退役插件身份。"""

from __future__ import annotations

from dataclasses import fields
from inspect import signature

from src.app.runtime.capabilities.material_flow.contracts.ng_reason import (
    BUILTIN_NG_REASONS,
    NgReasonDefinition,
    NgReasonSource,
    build_ng_reason_catalog,
)
from src.app.runtime.orchestration.models.runtime_hold import (
    NgReasonSource as PersistentNgReasonSource,
)
from src.app.runtime.orchestration.models.runtime_hold import (
    NgReturnItem,
    RuntimeHold,
)
from src.app.runtime.orchestration.models.runtime_hold_api import NgReasonOption, RuntimeHoldSummary
from src.app.runtime.orchestration.services.hold.runtime_hold_query_service import RuntimeHoldQueryService

_LEGAL_SOURCES = {"DEVICE_ERROR", "RUNTIME", "MANUAL"}
_PLUGIN_IDENTITY_FIELDS = {"plugin_key", "contract_version"}


def test_runtime_hold_and_ng_reason_contracts_expose_no_plugin_identity() -> None:
    """删除退役字段后，枚举、DTO、API 与 ORM 只保留通用 NG 合同。"""

    assert {source.value for source in NgReasonSource} == _LEGAL_SOURCES
    assert {source.value for source in PersistentNgReasonSource} == _LEGAL_SOURCES
    assert _PLUGIN_IDENTITY_FIELDS.isdisjoint(field.name for field in fields(NgReasonDefinition))
    assert _PLUGIN_IDENTITY_FIELDS.isdisjoint(RuntimeHoldSummary.model_fields)
    assert _PLUGIN_IDENTITY_FIELDS.isdisjoint(NgReasonOption.model_fields)
    assert _PLUGIN_IDENTITY_FIELDS.isdisjoint(RuntimeHold.__table__.columns.keys())
    assert _PLUGIN_IDENTITY_FIELDS.isdisjoint(NgReturnItem.__table__.columns.keys())


def test_builtin_ng_reasons_build_and_query_by_canonical_code() -> None:
    """内置 reason 仍可构建目录，并按 canonical code 供查询服务读取。"""

    assert not signature(build_ng_reason_catalog).parameters
    catalog = build_ng_reason_catalog()
    expected_by_code = {reason.canonical_code: reason for reason in BUILTIN_NG_REASONS}
    assert catalog.by_code == expected_by_code

    options = RuntimeHoldQueryService().list_ng_reasons()
    assert {option.code: option.source for option in options} == {
        "UNKNOWN_PHYSICAL_STATE": "RUNTIME",
        "OPERATOR_JUDGED_NG": "MANUAL",
        "RUNTIME_RECOVERY_NG": "RUNTIME",
    }
    assert all(set(option.model_dump()) == {"source", "code", "label", "maps_from"} for option in options)
