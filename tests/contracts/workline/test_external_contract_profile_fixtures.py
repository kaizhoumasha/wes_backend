"""P0-006 external contract profile fixtures 必须真实存在且可校验。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.external_contract_profile import FixtureCase

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "external_contracts" / "wms" / "default"
REQUIRED_CASES = {"success", "reject", "timeout", "duplicate", "missing_event_id"}


def test_wms_default_fixture_set_contains_required_cases():
    assert FIXTURE_ROOT.is_dir(), f"fixture_set.path 缺失: {FIXTURE_ROOT}"

    case_files = {path.stem: path for path in FIXTURE_ROOT.glob("*.json")}
    assert set(case_files) >= REQUIRED_CASES


def test_wms_default_fixtures_match_schema_and_profile_identity():
    for case_path in sorted(FIXTURE_ROOT.glob("*.json")):
        fixture = FixtureCase.model_validate(json.loads(case_path.read_text(encoding="utf-8")))

        assert fixture.provider_code == "WMS"
        assert fixture.contract_version == "2026-06-25"
        assert fixture.case_id == case_path.stem
