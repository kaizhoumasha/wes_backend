"""SMT 入库 handoff 原因码 catalog 合同测试。"""

from __future__ import annotations

import importlib
from typing import Any

import pytest


def _reason_module() -> Any:
    try:
        return importlib.import_module("src.app.workline.domain.services.smt_inbound_handoff_reason")
    except ModuleNotFoundError as exc:
        pytest.fail(f"缺少 SMT 入库 handoff 原因码模块: {exc}")


def test_smt_inbound_handoff_reason_catalog_covers_spec_categories() -> None:
    module = _reason_module()
    reason_code = getattr(module, "SmtInboundHandoffReasonCode", None)
    build_catalog = getattr(module, "build_smt_inbound_handoff_reason_catalog", None)
    if reason_code is None or build_catalog is None:
        pytest.fail("原因码模块必须导出 SmtInboundHandoffReasonCode 和 build_smt_inbound_handoff_reason_catalog")

    catalog = build_catalog()
    codes = set(catalog.by_code)

    assert {
        "RELEASE_FACT_MISSING",
        "RELEASE_SNAPSHOT_INVALID",
        "USAGE_INVALID",
        "WMS_RCS_REJECTED",
        "WMS_RCS_TIMEOUT",
        "WMS_RCS_RACK_RELEASE_ID_MISMATCH",
        "POST_EXCHANGE_RELATIONS_MISSING",
        "ROUTE_NOT_FOUND",
        "TARGET_WORKLINE_NOT_READY",
        "SOURCE_STATION_BUSY",
        "TARGET_SESSION_BUSY",
        "ECS_DEVICE_NOT_IDLE",
        "SOURCE_ITEM_CLAIM_CONFLICT",
        "INTERNAL_INBOX_ENVELOPE_INVALID",
        "SOURCE_PICK_EVENT_CREATE_FAILED",
        "SOURCE_PICK_COMMAND_NOT_CREATED",
        "SOURCE_PICK_INBOX_DEAD_LETTER",
        "PLUGIN_CONTRACT_INVALID",
    } <= codes
    assert {item.value for item in reason_code} <= codes


def test_smt_inbound_handoff_reason_catalog_returns_stable_failure_code_and_actions() -> None:
    module = _reason_module()
    reason_code = module.SmtInboundHandoffReasonCode
    catalog = module.build_smt_inbound_handoff_reason_catalog()

    route_missing = catalog.by_code[reason_code.ROUTE_NOT_FOUND.value]
    command_missing = catalog.by_code[reason_code.SOURCE_PICK_COMMAND_NOT_CREATED.value]
    dead_letter = catalog.by_code[reason_code.SOURCE_PICK_INBOX_DEAD_LETTER.value]

    assert route_missing.failure_code == "ROUTE_NOT_FOUND"
    assert route_missing.available_actions == ("REEVALUATE", "RELEASE_HOLD")
    assert command_missing.failure_code == "SOURCE_PICK_COMMAND_NOT_CREATED"
    assert command_missing.available_actions == ("RETRY_SOURCE_PICK", "RELEASE_HOLD")
    assert dead_letter.failure_code == "SOURCE_PICK_INBOX_DEAD_LETTER"
    assert dead_letter.available_actions == ("RETRY_SOURCE_PICK", "RELEASE_HOLD")
