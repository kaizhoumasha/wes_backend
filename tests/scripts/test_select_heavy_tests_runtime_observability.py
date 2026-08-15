import hashlib
from pathlib import Path

import pytest

from scripts.select_heavy_tests import load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "docs/architecture/heavy-test-impact.toml"


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/app/runtime/system_capabilities/replay.py",
        "src/app/runtime/orchestration/repositories/timeline_recorded_replay_repository.py",
    ],
)
def test_deleted_recorded_replay_paths_are_exact_none_tombstones(changed_path: str) -> None:
    config = load_config(MAPPING_PATH)
    mapping = next(mapping for mapping in config[1] if mapping.source_glob == changed_path)

    assert not (REPO_ROOT / changed_path).exists()
    assert mapping.heavy_tests == ()
    assert mapping.reviewed_content_sha256 is None
    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/app/runtime/system_capabilities/__init__.py",
        "src/app/runtime/orchestration/observability.py",
    ],
)
def test_surviving_runtime_observability_none_is_pinned_to_current_content(changed_path: str) -> None:
    config = load_config(MAPPING_PATH)
    mapping = next(mapping for mapping in config[1] if mapping.source_glob == changed_path)

    assert mapping.heavy_tests == ()
    assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []
