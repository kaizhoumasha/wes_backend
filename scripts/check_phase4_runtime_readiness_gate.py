"""Validate Phase 4 runtime readiness for the current development/test profile."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


PHASE4_SPEC_STATUS_TOKENS: dict[str, tuple[str, ...]] = {
    "cell-reservation-spec.md": ("P0", "开发/测试"),
    "material-location-query-spec.md": ("Wave1", "开发/测试"),
    "workline-active-objects-spec.md": ("Wave1", "开发/测试"),
    "sorter-inbound-capability-spec.md": ("本机 MOCK", "生产热路径未接入"),
    "smt-ng-wms-reconciliation-spec.md": ("本机 MOCK", "生产热路径未接入"),
}
PHASE4_MOCK_TEST_FILES = (
    "tests/mock/phase4/test_wave2_wave3_mock_acceptance.py",
    "tests/mock/phase4/test_sorter_inbound_mock_contracts.py",
)
PHASE4_RUNTIME_CAPABILITY_FILES = (
    "src/app/runtime/capabilities/phase4/sorter_inbound_preview_service.py",
    "tests/workline_runtime/test_sorter_inbound_preview_service.py",
)
RUNTIME_READINESS_PLAN = "docs/superpowers/plans/2026-07-04-phase4-runtime-readiness.md"
MAIN_PLAN = "docs/architecture/workline-and-plugin-restructuring.md"


@dataclass(frozen=True)
class Phase4ReadinessValidation:
    valid: bool
    reason: str
    invalid_specs: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    missing_tokens: tuple[str, ...] = ()
    stale_specs: tuple[str, ...] = ()
    production_hot_path_enabled: bool = False


@dataclass
class _ValidationAccumulator:
    invalid_specs: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    missing_tokens: list[str] = field(default_factory=list)
    stale_specs: list[str] = field(default_factory=list)
    production_hot_path_enabled: bool = False

    def as_result(self) -> Phase4ReadinessValidation:
        if self.missing_files:
            reason = "MISSING_PHASE4_READINESS_FILES"
        elif self.stale_specs:
            reason = "STALE_PHASE4_SPEC_STATUS"
        elif self.invalid_specs:
            reason = "INVALID_PHASE4_SPEC_STATUS"
        elif self.missing_tokens:
            reason = "MISSING_PHASE4_READINESS_TOKENS"
        elif self.production_hot_path_enabled:
            reason = "PHASE4_PRODUCTION_HOT_PATH_ENABLED"
        else:
            reason = "MOCK_PHASE4_RUNTIME_READINESS"
        return Phase4ReadinessValidation(
            valid=reason == "MOCK_PHASE4_RUNTIME_READINESS",
            reason=reason,
            invalid_specs=tuple(sorted(self.invalid_specs)),
            missing_files=tuple(sorted(self.missing_files)),
            missing_tokens=tuple(sorted(self.missing_tokens)),
            stale_specs=tuple(sorted(self.stale_specs)),
            production_hot_path_enabled=self.production_hot_path_enabled,
        )


def _read(repo_root: Path, relative_path: str) -> str:
    return (repo_root / relative_path).read_text(encoding="utf-8")


def _header(text: str) -> str:
    return "\n".join(text.splitlines()[:4])


def validate_mock_readiness(repo_root: Path) -> Phase4ReadinessValidation:
    accumulator = _ValidationAccumulator()

    for filename, required_tokens in PHASE4_SPEC_STATUS_TOKENS.items():
        relative_path = f"docs/architecture/{filename}"
        path = repo_root / relative_path
        if not path.exists():
            accumulator.missing_files.append(relative_path)
            continue
        header = _header(path.read_text(encoding="utf-8"))
        if "未实现" in header:
            accumulator.stale_specs.append(filename)
            continue
        missing_status_tokens = [token for token in required_tokens if token not in header]
        if missing_status_tokens:
            accumulator.invalid_specs.append(f"{filename}:{','.join(missing_status_tokens)}")

    for relative_path in (RUNTIME_READINESS_PLAN, MAIN_PLAN, *PHASE4_MOCK_TEST_FILES, *PHASE4_RUNTIME_CAPABILITY_FILES):
        if not (repo_root / relative_path).exists():
            accumulator.missing_files.append(relative_path)

    if not accumulator.missing_files:
        runtime_plan = _read(repo_root, RUNTIME_READINESS_PLAN)
        main_plan = _read(repo_root, MAIN_PLAN)
        mock_test_text = "\n".join(_read(repo_root, relative_path) for relative_path in PHASE4_MOCK_TEST_FILES)
        sorter_preview_service = _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[0])
        sorter_preview_test = _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[1])
        required_tokens_by_source = {
            RUNTIME_READINESS_PLAN: (
                "Wave2/Wave3 降级为本机开发环境 MOCK 验收",
                "不做生产接入",
                "开发/测试范围的 Phase4 runtime readiness gate 已关闭",
                "- [ ] Wave2 生产热路径",
                "- [ ] Wave3 生产热路径",
            ),
            MAIN_PLAN: (
                "### 10.5 Phase 4",
                "生产热路径",
                "production closure profile",
            ),
            "tests/mock/phase4": (
                "MOCK",
                "不代表生产热路径接入",
            ),
            PHASE4_RUNTIME_CAPABILITY_FILES[0]: (
                "LOCAL_MOCK_ONLY",
                "production_write_path",
                "legacy_plugin_entry_used",
            ),
            PHASE4_RUNTIME_CAPABILITY_FILES[1]: (
                "production_write_path",
                "legacy_plugin_entry_used",
                "WmsFulfillmentPort.notify_pkg_binding",
                "WmsInventoryTransactionPort.confirm_inbound",
            ),
        }
        source_texts = {
            RUNTIME_READINESS_PLAN: runtime_plan,
            MAIN_PLAN: main_plan,
            "tests/mock/phase4": mock_test_text,
            PHASE4_RUNTIME_CAPABILITY_FILES[0]: sorter_preview_service,
            PHASE4_RUNTIME_CAPABILITY_FILES[1]: sorter_preview_test,
        }
        for source, required_tokens in required_tokens_by_source.items():
            source_text = source_texts[source]
            accumulator.missing_tokens.extend(
                f"{source}:{token}" for token in required_tokens if token not in source_text
            )
        if "- [x] Wave2 生产热路径" in runtime_plan or "- [x] Wave3 生产热路径" in runtime_plan:
            accumulator.production_hot_path_enabled = True

    return accumulator.as_result()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readiness-profile",
        choices=("development-mock", "test-mock", "production"),
        default="development-mock",
        help="Readiness profile. Production is intentionally blocked until production hot path gates are explicit.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root to validate. Defaults to the script repository.",
    )
    return parser.parse_args(argv)


def _print_failure(validation: Phase4ReadinessValidation) -> None:
    print(f"Phase 4 runtime readiness mock gate failed: {validation.reason}")
    if validation.missing_files:
        print(f"missing_files={','.join(validation.missing_files)}")
    if validation.stale_specs:
        print(f"stale_specs={','.join(validation.stale_specs)}")
    if validation.invalid_specs:
        print(f"invalid_specs={','.join(validation.invalid_specs)}")
    if validation.missing_tokens:
        print(f"missing_tokens={','.join(validation.missing_tokens)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.readiness_profile == "production":
        print("Phase 4 runtime readiness production gate failed: PHASE4_PRODUCTION_HOT_PATH_NOT_ENABLED")
        return 2

    validation = validate_mock_readiness(Path(args.repo_root))
    if not validation.valid:
        _print_failure(validation)
        return 1

    print(
        "Phase 4 runtime readiness mock gate passed: "
        f"reason={validation.reason} readiness_profile={args.readiness_profile}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
