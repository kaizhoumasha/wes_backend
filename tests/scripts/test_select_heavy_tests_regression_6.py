from pathlib import Path

from scripts.select_heavy_tests import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFERRED_WMS_LIVE_TEST = "tests/integration/test_wms_mock_northbound_live.py"


def test_deferred_wms_live_acceptance_is_absent_from_core_tests_and_heavy_mapping() -> None:
    assert not (REPO_ROOT / DEFERRED_WMS_LIVE_TEST).exists()

    _ignore_globs, mappings = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    assert all(DEFERRED_WMS_LIVE_TEST not in mapping.heavy_tests for mapping in mappings)
