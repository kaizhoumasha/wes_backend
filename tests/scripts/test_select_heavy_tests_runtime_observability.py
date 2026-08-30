import hashlib
from pathlib import Path

from scripts.select_heavy_tests import load_config, select_heavy_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "docs/architecture/heavy-test-impact.toml"


def test_surviving_system_capability_surface_none_is_pinned_to_current_content() -> None:
    changed_path = "src/app/runtime/system_capabilities/__init__.py"
    config = load_config(MAPPING_PATH)
    mapping = next(mapping for mapping in config[1] if mapping.source_glob == changed_path)

    assert mapping.heavy_tests == ()
    assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []
