"""Validate Phase 4 runtime readiness for the requested evidence profile."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


PHASE4_SPEC_STATUS_TOKENS: dict[str, tuple[str, ...]] = {
    "cell-reservation-spec.md": ("P0", "开发/测试"),
    "material-location-query-spec.md": ("Wave1", "开发/测试"),
    "workline-active-objects-spec.md": ("Wave1", "开发/测试"),
    "sorter-inbound-capability-spec.md": ("runtime capability", "evidence profile"),
    "smt-ng-wms-reconciliation-spec.md": ("runtime capability", "evidence profile"),
}
PHASE4_MOCK_TEST_FILES = (
    "tests/mock/phase4/test_wave2_wave3_mock_acceptance.py",
    "tests/mock/phase4/test_sorter_inbound_mock_contracts.py",
)
PHASE4_RUNTIME_CAPABILITY_FILES = (
    "src/app/runtime/capabilities/phase4/sorter_inbound_preview_service.py",
    "tests/workline_runtime/test_sorter_inbound_preview_service.py",
    "src/app/runtime/capabilities/phase4/smt_ng_wms_reconciliation_preview_service.py",
    "tests/workline_runtime/test_smt_ng_wms_reconciliation_preview_service.py",
    "src/app/runtime/capabilities/phase4/sorter_inbound_runtime_service.py",
    "tests/workline_runtime/test_sorter_inbound_runtime_service.py",
    "src/app/runtime/capabilities/phase4/smt_ng_wms_reconciliation_runtime_service.py",
    "tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py",
)
RUNTIME_READINESS_PLAN = "docs/superpowers/plans/2026-07-04-phase4-runtime-readiness.md"
MAIN_PLAN = "docs/architecture/workline-and-plugin-restructuring.md"
DEVELOPMENT_READINESS_PROFILES = frozenset({"development", "development-mock", "test-mock"})
EVIDENCE_READINESS_PROFILES = frozenset({"simulator", "site", "production"})


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
            reason = "PHASE4_EVIDENCE_PROFILE_MARKED_COMPLETE"
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
        reconciliation_preview_service = _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[2])
        reconciliation_preview_test = _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[3])
        required_tokens_by_source = {
            RUNTIME_READINESS_PLAN: (
                "Wave2/Wave3 后续目标是 production-capable runtime path",
                "外部 provider 可替换",
                "开发/测试范围的 Phase4 runtime readiness gate 已关闭",
                "- [x] Wave2 runtime capability builder",
                "- [x] Wave3 runtime capability builder",
                "- [ ] Wave2 evidence profile",
                "- [ ] Wave3 evidence profile",
            ),
            MAIN_PLAN: (
                "### 10.5 Phase 4",
                "production-capable runtime path",
                "evidence profile",
            ),
            "tests/mock/phase4": (
                "MOCK",
                "不代表 evidence profile 闭合",
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
            PHASE4_RUNTIME_CAPABILITY_FILES[2]: (
                "LOCAL_MOCK_ONLY",
                "production_write_path",
                "legacy_plugin_entry_used",
                "RuntimeHold",
            ),
            PHASE4_RUNTIME_CAPABILITY_FILES[3]: (
                "production_write_path",
                "legacy_plugin_entry_used",
                "IDEMPOTENT_DUPLICATE",
                "RuntimeHold",
            ),
            PHASE4_RUNTIME_CAPABILITY_FILES[4]: (
                "RuntimeIntent",
                "WmsFulfillmentPort.notify_pkg_binding",
                "WmsInventoryTransactionPort.confirm_inbound",
                "provider-contract",
            ),
            PHASE4_RUNTIME_CAPABILITY_FILES[5]: (
                "RuntimeIntent",
                "WmsFulfillmentPort.notify_pkg_binding",
                "WmsInventoryTransactionPort.confirm_inbound",
                "provider-contract",
            ),
            PHASE4_RUNTIME_CAPABILITY_FILES[6]: (
                "RuntimeIntent",
                "RuntimeInbox",
                "provider-contract",
            ),
            PHASE4_RUNTIME_CAPABILITY_FILES[7]: (
                "RuntimeIntent",
                "RuntimeInbox",
                "provider-contract",
            ),
        }
        source_texts = {
            RUNTIME_READINESS_PLAN: runtime_plan,
            MAIN_PLAN: main_plan,
            "tests/mock/phase4": mock_test_text,
            PHASE4_RUNTIME_CAPABILITY_FILES[0]: sorter_preview_service,
            PHASE4_RUNTIME_CAPABILITY_FILES[1]: sorter_preview_test,
            PHASE4_RUNTIME_CAPABILITY_FILES[2]: reconciliation_preview_service,
            PHASE4_RUNTIME_CAPABILITY_FILES[3]: reconciliation_preview_test,
            PHASE4_RUNTIME_CAPABILITY_FILES[4]: _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[4]),
            PHASE4_RUNTIME_CAPABILITY_FILES[5]: _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[5]),
            PHASE4_RUNTIME_CAPABILITY_FILES[6]: _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[6]),
            PHASE4_RUNTIME_CAPABILITY_FILES[7]: _read(repo_root, PHASE4_RUNTIME_CAPABILITY_FILES[7]),
        }
        for source, required_tokens in required_tokens_by_source.items():
            source_text = source_texts[source]
            accumulator.missing_tokens.extend(
                f"{source}:{token}" for token in required_tokens if token not in source_text
            )
        if "- [x] Wave2 evidence profile" in runtime_plan or "- [x] Wave3 evidence profile" in runtime_plan:
            accumulator.production_hot_path_enabled = True

    return accumulator.as_result()


def validate_runtime_evidence_artifact(artifact_path: Path, *, evidence_profile: str) -> tuple[bool, str]:
    """Validate Phase4 runtime evidence without changing runtime behavior."""

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "INVALID_PHASE4_RUNTIME_EVIDENCE_ARTIFACT"
    if not isinstance(artifact, dict):
        return False, "INVALID_PHASE4_RUNTIME_EVIDENCE_ARTIFACT"

    profile = artifact.get("profile")
    if not isinstance(profile, dict) or profile.get("name") != evidence_profile:
        return False, "MISMATCHED_PHASE4_RUNTIME_EVIDENCE_PROFILE"

    capabilities = artifact.get("capabilities")
    if not isinstance(capabilities, list) or not {"sorter_inbound", "smt_ng_wms_reconciliation"}.issubset(
        {str(item) for item in capabilities}
    ):
        return False, "MISSING_PHASE4_RUNTIME_CAPABILITIES"

    effect_path = _string_set(artifact.get("effect_path"))
    if not {
        "RuntimeIntentLog",
        "WmsFulfillmentPort.notify_pkg_binding",
        "WmsInventoryTransactionPort.confirm_inbound",
    }.issubset(effect_path):
        return False, "MISSING_PHASE4_RUNTIME_EFFECT_PATH"

    callback_path = _string_set(artifact.get("callback_path"))
    if "RuntimeInbox" not in callback_path:
        return False, "MISSING_PHASE4_RUNTIME_CALLBACK_PATH"

    invariants = _string_set(artifact.get("service_behavior_invariant"))
    if "provider-contract" not in invariants:
        return False, "MISSING_PHASE4_RUNTIME_PROVIDER_CONTRACT_INVARIANT"

    return True, "PHASE4_RUNTIME_EVIDENCE_READY"


def _string_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if str(item)}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readiness-profile",
        choices=("development", "development-mock", "test-mock", "simulator", "site", "production"),
        default="development-mock",
        help="Readiness profile. Profiles change evidence requirements only, not runtime service behavior.",
    )
    parser.add_argument("--phase4-runtime-evidence-artifact", help="Path to the Phase4 runtime evidence artifact JSON.")
    parser.add_argument("--p0-e2e-artifact", help="Path to the Phase3 production P0 E2E artifact JSON.")
    parser.add_argument("--benchmark-artifact", help="Path to the Phase3 production-scale benchmark artifact JSON.")
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

    validation = validate_mock_readiness(Path(args.repo_root))
    if not validation.valid:
        _print_failure(validation)
        return 1

    if args.readiness_profile in DEVELOPMENT_READINESS_PROFILES:
        print(
            "Phase 4 runtime readiness mock gate passed: "
            f"reason={validation.reason} readiness_profile={args.readiness_profile}"
        )
        return 0

    if args.readiness_profile in EVIDENCE_READINESS_PROFILES:
        if not args.phase4_runtime_evidence_artifact:
            print(
                "Phase 4 runtime readiness evidence gate failed: "
                f"MISSING_PHASE4_RUNTIME_EVIDENCE_ARTIFACT evidence_profile={args.readiness_profile}"
            )
            return 2
        evidence_valid, evidence_reason = validate_runtime_evidence_artifact(
            Path(args.phase4_runtime_evidence_artifact),
            evidence_profile=args.readiness_profile,
        )
        if not evidence_valid:
            print(
                "Phase 4 runtime readiness evidence gate failed: "
                f"{evidence_reason} evidence_profile={args.readiness_profile}"
            )
            return 1
        if args.readiness_profile == "production":
            from src.app.runtime.orchestration.phase3_closure_gate import RuntimePhase3ClosureGate

            artifact_paths = {}
            if args.p0_e2e_artifact:
                artifact_paths["p0_e2e"] = Path(args.p0_e2e_artifact)
            if args.benchmark_artifact:
                artifact_paths["benchmark"] = Path(args.benchmark_artifact)
            closure_validation = RuntimePhase3ClosureGate().validate_artifact_files(
                artifact_paths,
                closure_profile="production",
            )
            if not closure_validation.valid:
                print(
                    "Phase 4 runtime readiness evidence gate failed: "
                    f"{closure_validation.reason} evidence_profile=production"
                )
                return 2
        print(
            "Phase 4 runtime readiness evidence gate passed: "
            f"reason={evidence_reason} evidence_profile={args.readiness_profile}"
        )
        return 0

    print(
        "Phase 4 runtime readiness mock gate passed: "
        f"reason={validation.reason} readiness_profile={args.readiness_profile}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
