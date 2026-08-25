"""HEAVY selector 配置校验的性能回归保护。"""

from pathlib import Path
from time import process_time

from scripts.select_heavy_tests import _patterns_overlap, _segment_patterns_overlap, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_segment_pattern_overlap_reuses_identical_calculation() -> None:
    _segment_patterns_overlap.cache_clear()

    assert _segment_patterns_overlap("runtime_*", "runtime_inbox") is True
    first_call = _segment_patterns_overlap.cache_info()

    assert _segment_patterns_overlap("runtime_*", "runtime_inbox") is True
    second_call = _segment_patterns_overlap.cache_info()

    assert second_call.hits == first_call.hits + 1


def test_full_pattern_overlap_reuses_identical_calculation() -> None:
    _patterns_overlap.cache_clear()

    assert _patterns_overlap("src/app/**", "src/app/admin/**") is True
    first_call = _patterns_overlap.cache_info()

    assert _patterns_overlap("src/app/**", "src/app/admin/**") is True
    second_call = _patterns_overlap.cache_info()

    assert second_call.hits == first_call.hits + 1


def test_repository_mapping_validation_stays_within_two_seconds() -> None:
    mapping_path = REPO_ROOT / "docs/architecture/heavy-test-impact.toml"
    _patterns_overlap.cache_clear()
    _segment_patterns_overlap.cache_clear()
    started_at = process_time()

    for _ in range(3):
        load_config(mapping_path)

    elapsed_seconds = process_time() - started_at

    assert elapsed_seconds < 2.0, f"HEAVY selector 配置校验耗时 {elapsed_seconds:.2f}s，超过 2.00s 预算"
