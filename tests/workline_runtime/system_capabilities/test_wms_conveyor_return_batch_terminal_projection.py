"""E13 terminal/reconciliation 仅通过领域 projector direct API 分派。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.services.wms_fulfillment_domain_projector import (
    WmsFulfillmentDomainProjector,
)
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
from tests.mock.wms_northbound_contract import build_typed_result
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


class _ReturnBatch:
    def __init__(self) -> None:
        self.success_calls: list[dict[str, Any]] = []
        self.reconciliation_calls: list[dict[str, Any]] = []

    async def project_success(self, db: Any, **kwargs: Any) -> None:
        self.success_calls.append({"db": db, **kwargs})

    async def project_reconciliation(self, db: Any, **kwargs: Any) -> None:
        self.reconciliation_calls.append({"db": db, **kwargs})


def _success_result_payload() -> dict[str, Any]:
    return build_typed_result(
        E13,
        REQUEST_FIXTURES[E13],
        source_version=7,
        completed_at="2026-07-30T10:00:00+00:00",
        provider_reference="provider-e13-terminal",
    )


def _partial_result_payload() -> dict[str, Any]:
    payload = deepcopy(_success_result_payload())
    payload["task_outcome"] = "FAILED_AFTER_EXECUTION"
    payload["items"][0]["item_outcome"] = "FAILED"
    return payload


@pytest.mark.asyncio
async def test_e13_direct_terminal_delegate_projects_only_all_success() -> None:
    return_batch = _ReturnBatch()
    projector = WmsFulfillmentDomainProjector(conveyor_return_batch=return_batch)
    db = SimpleNamespace()

    await projector.project_conveyor_return_terminal_result(
        db,
        operation=WMS_OPERATION_BY_IDENTITY[E13],
        request_payload=REQUEST_FIXTURES[E13],
        result_payload=_success_result_payload(),
        occurred_at_ms=10_000,
        source_event_id="e13-terminal-success",
    )

    assert len(return_batch.success_calls) == 1
    assert return_batch.success_calls[0]["request"].candidate_digest == REQUEST_FIXTURES[E13]["candidate_digest"]
    assert return_batch.success_calls[0]["result"].task_outcome == "SUCCESS"
    assert return_batch.reconciliation_calls == []


@pytest.mark.asyncio
async def test_e13_direct_reconciliation_delegate_requires_non_success_and_open_case() -> None:
    return_batch = _ReturnBatch()
    projector = WmsFulfillmentDomainProjector(conveyor_return_batch=return_batch)
    db = SimpleNamespace()

    await projector.project_conveyor_return_reconciliation_result(
        db,
        operation=WMS_OPERATION_BY_IDENTITY[E13],
        request_payload=REQUEST_FIXTURES[E13],
        result_payload=_partial_result_payload(),
        reconciliation_case_id=71,
        occurred_at_ms=10_100,
        source_event_id="e13-terminal-partial",
        reason_code="WMS_FULFILLMENT_TERMINAL_NON_SUCCESS",
    )

    assert len(return_batch.reconciliation_calls) == 1
    call = return_batch.reconciliation_calls[0]
    assert call["reconciliation_case_id"] == 71
    assert call["result"].task_outcome == "FAILED_AFTER_EXECUTION"
    assert return_batch.success_calls == []
