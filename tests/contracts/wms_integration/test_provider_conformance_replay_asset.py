"""WMS Provider replay asset 的独立 pin、顺序与报告 provenance 合同。"""

from __future__ import annotations

import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILES
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    QUERY_INVENTORY_CONFORMANCE_CASES,
    ConformanceTarget,
    build_wms_conformance_report,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ASSET_PATH = REPO_ROOT / "tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json"
PINNED_ASSET_DIGEST = "4584ece449cdcfa69f6a46ac4315b3f11a285f3f832a82bc04685c21ac22bf52"
SANDBOX_PROFILE = WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.sandbox"]
GENERATED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


def _replay_support():
    return importlib.import_module("tests.support.wms_provider_replay")


def _asset_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        {"schema_version": payload["schema_version"], "records": payload["records"]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_replay_loader_validates_record_identity_order_and_code_pin(tmp_path: Path) -> None:
    replay_support = _replay_support()
    loaded = replay_support.load_query_inventory_replay_fixture()
    expected_ids = tuple(case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES)

    assert replay_support.QUERY_INVENTORY_REPLAY_ASSET_DIGEST == PINNED_ASSET_DIGEST
    assert loaded.digest == PINNED_ASSET_DIGEST
    assert tuple(record.case_id for record in loaded.records) == expected_ids

    reordered = json.loads(ASSET_PATH.read_text(encoding="utf-8"))
    reordered["records"][0], reordered["records"][1] = reordered["records"][1], reordered["records"][0]
    reordered["digest"] = _asset_digest(reordered)
    reordered_path = tmp_path / "reordered.json"
    reordered_path.write_text(json.dumps(reordered), encoding="utf-8")
    with pytest.raises(ValueError, match="identity order"):
        replay_support.load_query_inventory_replay_fixture(reordered_path)

    repinned_by_asset = json.loads(ASSET_PATH.read_text(encoding="utf-8"))
    repinned_by_asset["records"][0]["items"][0]["available_quantity"] = "8.25"
    repinned_by_asset["digest"] = _asset_digest(repinned_by_asset)
    repinned_path = tmp_path / "repinned.json"
    repinned_path.write_text(json.dumps(repinned_by_asset), encoding="utf-8")
    with pytest.raises(ValueError, match="code-pinned digest"):
        replay_support.load_query_inventory_replay_fixture(repinned_path)


@pytest.mark.asyncio
async def test_replay_report_carries_and_verifies_the_actual_asset_digest() -> None:
    replay_support = _replay_support()
    factory = replay_support.QueryInventoryReplayFactory()
    observations = tuple([await factory.execute(case) for case in QUERY_INVENTORY_CONFORMANCE_CASES])

    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=observations,
        target=ConformanceTarget.REPLAY,
        profile=SANDBOX_PROFILE,
        fixture_digest=factory.asset_digest,
        generated_at=GENERATED_AT,
    )

    assert report.fixture_digest == replay_support.QUERY_INVENTORY_REPLAY_ASSET_DIGEST
    assert replay_support.verify_query_inventory_replay_report(report.model_dump(mode="json")) == report

    scripted_digest_payload = report.model_dump(mode="json")
    scripted_digest_payload["fixture_digest"] = "a" * 64
    without_report_digest = {key: value for key, value in scripted_digest_payload.items() if key != "report_digest"}
    canonical = json.dumps(without_report_digest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    scripted_digest_payload["report_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match=r"replay.*asset digest"):
        replay_support.verify_query_inventory_replay_report(scripted_digest_payload)
