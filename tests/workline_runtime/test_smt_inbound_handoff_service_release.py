"""SMT 入库 handoff release fact 幂等入口测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
)


def _release_bin(slot_code: str, usage: float = 0.25) -> dict[str, Any]:
    return {
        "slot_code": slot_code,
        "bin_code": f"BIN-{slot_code}",
        "usage": usage,
        "status": "IN_USE",
        "cells": [
            {
                "bin_code": f"BIN-{slot_code}",
                "bin_cell_index": 1,
                "bin_cell_code": f"BIN-{slot_code}-1",
                "status": "OCCUPIED",
                "material_identity_key": f"MAT-{slot_code}",
                "pkg_code": f"PKG-{slot_code}",
                "reel_thickness_mm": "1.2",
            }
        ],
    }


def _release_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "rack_release_id": "release-001",
        "single_layer_rack_code": "RACK-001",
        "source_workline_id": 1001,
        "source_workline_code": "WL-SMT-ROUGH-01",
        "release_reason_code": "NO_COMPATIBLE_OR_EMPTY_CELL",
        "bin_snapshots": [_release_bin(slot) for slot in ("A", "B", "C", "D")],
        "trace_id": "trace-release-001",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_or_get_from_release_is_idempotent_by_rack_release_id(db_session: Any) -> None:
    from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService

    service = SmtInboundHandoffService()

    first = await service.create_or_get_from_release(db_session, **_release_payload())
    second = await service.create_or_get_from_release(
        db_session,
        **_release_payload(source_workline_code="WL-SMT-ROUGH-CHANGED"),
    )

    assert first.id == second.id
    assert first.demand_key == "smt-inbound-handoff:release-001"
    assert first.status == SmtInboundHandoffDemandStatus.CREATED
    assert first.source_workline_code == "WL-SMT-ROUGH-01"

    demands = (await db_session.execute(SmtInboundHandoffDemand.__table__.select())).all()
    source_items = (await db_session.execute(SmtInboundHandoffSourceItem.__table__.select())).all()
    assert len(demands) == 1
    assert len(source_items) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "failure_code"),
    [
        ({"rack_release_id": None}, "RELEASE_FACT_MISSING"),
        ({"single_layer_rack_code": None}, "RELEASE_FACT_MISSING"),
        ({"bin_snapshots": []}, "RELEASE_SNAPSHOT_INVALID"),
        ({"bin_snapshots": [_release_bin("A", usage=1.25)]}, "USAGE_INVALID"),
    ],
)
async def test_create_or_get_from_release_manual_holds_missing_or_invalid_release_fact(
    db_session: Any,
    overrides: dict[str, Any],
    failure_code: str,
) -> None:
    from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService

    demand = await SmtInboundHandoffService().create_or_get_from_release(
        db_session,
        **_release_payload(**overrides),
    )

    assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert demand.failure_code == failure_code
    assert demand.failure_message


@pytest.mark.asyncio
async def test_handoff_repository_create_or_get_by_release_returns_existing_demand(db_session: Any) -> None:
    from src.app.workline.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository

    repository = SmtInboundHandoffRepository()
    data = {
        "demand_key": "smt-inbound-handoff:release-repo-001",
        "rack_release_id": "release-repo-001",
        "single_layer_rack_code": "RACK-REPO-001",
        "source_workline_code": "WL-SMT-ROUGH-01",
        "bin_snapshots_json": {"bins": [_release_bin("A")]},
    }

    first = await repository.create_or_get_demand_by_release(db_session, data)
    second = await repository.create_or_get_demand_by_release(
        db_session,
        data | {"source_workline_code": "WL-SMT-ROUGH-CHANGED"},
    )

    assert first.id == second.id
    assert second.source_workline_code == "WL-SMT-ROUGH-01"


def test_handoff_service_and_repository_are_exported() -> None:
    from src.app.workline import repositories, services

    assert services.SmtInboundHandoffService is not None
    assert services.smt_inbound_handoff_service is not None
    assert repositories.SmtInboundHandoffRepository is not None
    assert repositories.smt_inbound_handoff_repository is not None
