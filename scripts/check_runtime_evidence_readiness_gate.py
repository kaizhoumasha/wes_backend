"""Validate Runtime evidence readiness for the requested evidence profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


RUNTIME_SPEC_STATUS_TOKENS: dict[str, tuple[str, ...]] = {
    "cell-reservation-spec.md": ("P0", "开发/测试"),
    "material-location-query-spec.md": ("Wave1", "开发/测试"),
    "workline-active-objects-spec.md": ("Wave1", "开发/测试"),
    "sorter-inbound-capability-spec.md": ("runtime capability", "evidence profile"),
    "smt-ng-wms-reconciliation-spec.md": ("runtime capability", "evidence profile"),
}
RUNTIME_MOCK_TEST_FILES = (
    "tests/mock/material_flow/test_wave2_wave3_mock_acceptance.py",
    "tests/mock/material_flow/test_sorter_inbound_mock_contracts.py",
)
RUNTIME_CAPABILITY_FILES = (
    "src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py",
    "tests/workline_runtime/test_sorter_inbound_preview_service.py",
    "src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_preview_service.py",
    "tests/workline_runtime/test_smt_ng_wms_reconciliation_preview_service.py",
    "src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py",
    "tests/workline_runtime/test_sorter_inbound_runtime_service.py",
    "src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_runtime_service.py",
    "tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py",
)
RUNTIME_READINESS_PLAN = "docs/superpowers/plans/2026-07-04-runtime-evidence-readiness.md"
MAIN_PLAN = "docs/architecture/workline-and-plugin-restructuring.md"
DEVELOPMENT_READINESS_PROFILES = frozenset({"development", "development-mock", "test-mock"})
EVIDENCE_READINESS_PROFILES = frozenset({"simulator", "site", "production"})
SITE_PRODUCTION_EVIDENCE_KEYS = (
    "provider_contracts.sorter_inbound",
    "provider_contracts.smt_ng_wms_reconciliation",
    "effect_dispatch_trace",
    "callback_worker_trace",
    "runtime_hold_reconciliation_trace",
    "benchmark",
)


@dataclass(frozen=True)
class RuntimeEvidenceReadinessValidation:
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

    def as_result(self) -> RuntimeEvidenceReadinessValidation:
        if self.missing_files:
            reason = "MISSING_RUNTIME_READINESS_FILES"
        elif self.stale_specs:
            reason = "STALE_RUNTIME_SPEC_STATUS"
        elif self.invalid_specs:
            reason = "INVALID_RUNTIME_SPEC_STATUS"
        elif self.missing_tokens:
            reason = "MISSING_RUNTIME_READINESS_TOKENS"
        elif self.production_hot_path_enabled:
            reason = "RUNTIME_EVIDENCE_PROFILE_MARKED_COMPLETE"
        else:
            reason = "MOCK_RUNTIME_EVIDENCE_READINESS"
        return RuntimeEvidenceReadinessValidation(
            valid=reason == "MOCK_RUNTIME_EVIDENCE_READINESS",
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


def validate_mock_readiness(repo_root: Path) -> RuntimeEvidenceReadinessValidation:
    accumulator = _ValidationAccumulator()

    for filename, required_tokens in RUNTIME_SPEC_STATUS_TOKENS.items():
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

    for relative_path in (RUNTIME_READINESS_PLAN, MAIN_PLAN, *RUNTIME_MOCK_TEST_FILES, *RUNTIME_CAPABILITY_FILES):
        if not (repo_root / relative_path).exists():
            accumulator.missing_files.append(relative_path)

    if not accumulator.missing_files:
        runtime_plan = _read(repo_root, RUNTIME_READINESS_PLAN)
        main_plan = _read(repo_root, MAIN_PLAN)
        mock_test_text = "\n".join(_read(repo_root, relative_path) for relative_path in RUNTIME_MOCK_TEST_FILES)
        sorter_preview_service = _read(repo_root, RUNTIME_CAPABILITY_FILES[0])
        sorter_preview_test = _read(repo_root, RUNTIME_CAPABILITY_FILES[1])
        reconciliation_preview_service = _read(repo_root, RUNTIME_CAPABILITY_FILES[2])
        reconciliation_preview_test = _read(repo_root, RUNTIME_CAPABILITY_FILES[3])
        required_tokens_by_source = {
            RUNTIME_READINESS_PLAN: (
                "Wave2/Wave3 后续目标是 production-capable runtime path",
                "外部 provider 可替换",
                "开发/测试范围的 Runtime evidence readiness gate 已关闭",
                "- [x] Wave2 runtime capability builder",
                "- [x] Wave3 runtime capability builder",
                "- [x] Wave2 evidence profile gate",
                "- [x] Wave3 evidence profile gate",
                "证据文件本身属于",
            ),
            MAIN_PLAN: (
                "### 10.5 Phase 4",
                "production-capable runtime path",
                "evidence manifest gate",
            ),
            "tests/mock/material_flow": (
                "MOCK",
                "不代表 evidence profile 闭合",
            ),
            RUNTIME_CAPABILITY_FILES[0]: (
                "LOCAL_MOCK_ONLY",
                "production_write_path",
                "legacy_plugin_entry_used",
            ),
            RUNTIME_CAPABILITY_FILES[1]: (
                "production_write_path",
                "legacy_plugin_entry_used",
                "WmsFulfillmentPort.notify_pkg_binding",
                "WmsInventoryTransactionPort.confirm_inbound",
            ),
            RUNTIME_CAPABILITY_FILES[2]: (
                "LOCAL_MOCK_ONLY",
                "production_write_path",
                "legacy_plugin_entry_used",
                "RuntimeHold",
            ),
            RUNTIME_CAPABILITY_FILES[3]: (
                "production_write_path",
                "legacy_plugin_entry_used",
                "IDEMPOTENT_DUPLICATE",
                "RuntimeHold",
            ),
            RUNTIME_CAPABILITY_FILES[4]: (
                "RuntimeIntent",
                "WmsFulfillmentPort.notify_pkg_binding",
                "WmsInventoryTransactionPort.confirm_inbound",
                "provider-contract",
            ),
            RUNTIME_CAPABILITY_FILES[5]: (
                "RuntimeIntent",
                "WmsFulfillmentPort.notify_pkg_binding",
                "WmsInventoryTransactionPort.confirm_inbound",
                "provider-contract",
            ),
            RUNTIME_CAPABILITY_FILES[6]: (
                "RuntimeIntent",
                "RuntimeInbox",
                "provider-contract",
            ),
            RUNTIME_CAPABILITY_FILES[7]: (
                "RuntimeIntent",
                "RuntimeInbox",
                "provider-contract",
            ),
        }
        source_texts = {
            RUNTIME_READINESS_PLAN: runtime_plan,
            MAIN_PLAN: main_plan,
            "tests/mock/material_flow": mock_test_text,
            RUNTIME_CAPABILITY_FILES[0]: sorter_preview_service,
            RUNTIME_CAPABILITY_FILES[1]: sorter_preview_test,
            RUNTIME_CAPABILITY_FILES[2]: reconciliation_preview_service,
            RUNTIME_CAPABILITY_FILES[3]: reconciliation_preview_test,
            RUNTIME_CAPABILITY_FILES[4]: _read(repo_root, RUNTIME_CAPABILITY_FILES[4]),
            RUNTIME_CAPABILITY_FILES[5]: _read(repo_root, RUNTIME_CAPABILITY_FILES[5]),
            RUNTIME_CAPABILITY_FILES[6]: _read(repo_root, RUNTIME_CAPABILITY_FILES[6]),
            RUNTIME_CAPABILITY_FILES[7]: _read(repo_root, RUNTIME_CAPABILITY_FILES[7]),
        }
        for source, required_tokens in required_tokens_by_source.items():
            source_text = source_texts[source]
            accumulator.missing_tokens.extend(
                f"{source}:{token}" for token in required_tokens if token not in source_text
            )
    return accumulator.as_result()


def validate_runtime_evidence_artifact(artifact_path: Path, *, evidence_profile: str) -> tuple[bool, str]:
    """Validate Runtime evidence without changing runtime behavior."""

    artifact = _load_runtime_evidence_artifact(artifact_path)
    if artifact is None:
        return False, "INVALID_RUNTIME_EVIDENCE_ARTIFACT"

    failure_reason = _basic_runtime_evidence_failure(artifact, evidence_profile=evidence_profile)
    if failure_reason is None and evidence_profile in {"site", "production"}:
        failure_reason = _site_production_evidence_failure(artifact_path=artifact_path, artifact=artifact)
    if failure_reason is not None:
        return False, failure_reason

    return True, "RUNTIME_EVIDENCE_READY"


def _load_runtime_evidence_artifact(artifact_path: Path) -> dict[str, object] | None:
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(artifact, dict):
        return None
    return artifact


def _basic_runtime_evidence_failure(artifact: Mapping[str, object], *, evidence_profile: str) -> str | None:
    profile = artifact.get("profile")
    if not isinstance(profile, dict) or profile.get("name") != evidence_profile:
        return "MISMATCHED_RUNTIME_EVIDENCE_PROFILE"

    capabilities = artifact.get("capabilities")
    if not isinstance(capabilities, list) or not {"sorter_inbound", "smt_ng_wms_reconciliation"}.issubset(
        {str(item) for item in capabilities}
    ):
        return "MISSING_RUNTIME_EVIDENCE_CAPABILITIES"

    effect_path = _string_set(artifact.get("effect_path"))
    if not {
        "RuntimeIntentLog",
        "WmsFulfillmentPort.notify_pkg_binding",
        "WmsInventoryTransactionPort.confirm_inbound",
    }.issubset(effect_path):
        return "MISSING_RUNTIME_EVIDENCE_EFFECT_PATH"

    callback_path = _string_set(artifact.get("callback_path"))
    if "RuntimeInbox" not in callback_path:
        return "MISSING_RUNTIME_EVIDENCE_CALLBACK_PATH"

    invariants = _string_set(artifact.get("service_behavior_invariant"))
    if "provider-contract" not in invariants:
        return "MISSING_RUNTIME_EVIDENCE_PROVIDER_CONTRACT_INVARIANT"

    return None


def _site_production_evidence_failure(*, artifact_path: Path, artifact: Mapping[str, object]) -> str | None:
    manifest = artifact.get("evidence_manifest")
    if not isinstance(manifest, Mapping):
        return "MISSING_RUNTIME_EVIDENCE_MANIFEST"
    missing_manifest_keys = _missing_manifest_keys(manifest)
    if missing_manifest_keys:
        return "MISSING_RUNTIME_EVIDENCE_MANIFEST"
    missing_evidence_files = _missing_manifest_evidence_files(base_dir=artifact_path.parent, manifest=manifest)
    if missing_evidence_files:
        return "MISSING_RUNTIME_EVIDENCE_FILES"
    missing_hashes = _missing_manifest_evidence_hashes(manifest)
    if missing_hashes:
        return "MISSING_RUNTIME_EVIDENCE_HASHES"
    mismatched_hashes = _mismatched_manifest_evidence_hashes(base_dir=artifact_path.parent, manifest=manifest)
    if mismatched_hashes:
        return "MISMATCHED_RUNTIME_EVIDENCE_HASHES"
    return None


def _string_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if str(item)}


def _missing_manifest_keys(manifest: Mapping[str, object]) -> tuple[str, ...]:
    missing_keys: list[str] = []
    for manifest_key in SITE_PRODUCTION_EVIDENCE_KEYS:
        entry = _manifest_entry(manifest, manifest_key)
        if not isinstance(entry, Mapping) or not _has_evidence_path(entry):
            missing_keys.append(manifest_key)
    return tuple(missing_keys)


def _missing_manifest_evidence_files(*, base_dir: Path, manifest: Mapping[str, object]) -> tuple[str, ...]:
    missing_files: list[str] = []
    for manifest_key in SITE_PRODUCTION_EVIDENCE_KEYS:
        entry = _manifest_entry(manifest, manifest_key)
        if not isinstance(entry, Mapping):
            continue
        raw_path = entry.get("evidence")
        if not _evidence_file_exists(base_dir=base_dir, raw_path=raw_path):
            missing_files.append(manifest_key)
    return tuple(missing_files)


def _missing_manifest_evidence_hashes(manifest: Mapping[str, object]) -> tuple[str, ...]:
    missing_hashes: list[str] = []
    for manifest_key in SITE_PRODUCTION_EVIDENCE_KEYS:
        entry = _manifest_entry(manifest, manifest_key)
        if not isinstance(entry, Mapping):
            continue
        if not _is_sha256_hex(entry.get("evidence_sha256")):
            missing_hashes.append(manifest_key)
    return tuple(missing_hashes)


def _mismatched_manifest_evidence_hashes(*, base_dir: Path, manifest: Mapping[str, object]) -> tuple[str, ...]:
    mismatched_hashes: list[str] = []
    for manifest_key in SITE_PRODUCTION_EVIDENCE_KEYS:
        entry = _manifest_entry(manifest, manifest_key)
        if not isinstance(entry, Mapping):
            continue
        evidence_hash = _hash_evidence_file(base_dir=base_dir, raw_path=entry.get("evidence"))
        if evidence_hash != entry.get("evidence_sha256"):
            mismatched_hashes.append(manifest_key)
    return tuple(mismatched_hashes)


def _manifest_entry(manifest: Mapping[str, object], dotted_key: str) -> object:
    current: object = manifest
    for key_part in dotted_key.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key_part)
    return current


def _has_evidence_path(entry: Mapping[str, object]) -> bool:
    raw_path = entry.get("evidence")
    return isinstance(raw_path, str) and bool(raw_path.strip())


def _is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _evidence_file_exists(*, base_dir: Path, raw_path: object) -> bool:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    evidence_path = Path(raw_path)
    if not evidence_path.is_absolute():
        evidence_path = base_dir / evidence_path
    return evidence_path.is_file()


def _hash_evidence_file(*, base_dir: Path, raw_path: object) -> str | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    evidence_path = Path(raw_path)
    if not evidence_path.is_absolute():
        evidence_path = base_dir / evidence_path
    try:
        return hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    except OSError:
        return None


def _ensure_repo_root_on_path(repo_root: Path) -> None:
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readiness-profile",
        choices=("development", "development-mock", "test-mock", "simulator", "site", "production"),
        default="development-mock",
        help="Readiness profile. Profiles change evidence requirements only, not runtime service behavior.",
    )
    parser.add_argument("--runtime-evidence-artifact", help="Path to the Runtime evidence artifact JSON.")
    parser.add_argument("--p0-e2e-artifact", help="Path to the runtime production E2E artifact JSON.")
    parser.add_argument("--benchmark-artifact", help="Path to the runtime production-scale benchmark artifact JSON.")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root to validate. Defaults to the script repository.",
    )
    return parser.parse_args(argv)


def _print_failure(validation: RuntimeEvidenceReadinessValidation) -> None:
    print(f"Runtime evidence readiness mock gate failed: {validation.reason}")
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

    repo_root = Path(args.repo_root)
    validation = validate_mock_readiness(repo_root)
    if not validation.valid:
        _print_failure(validation)
        return 1

    if args.readiness_profile in DEVELOPMENT_READINESS_PROFILES:
        print(
            "Runtime evidence readiness mock gate passed: "
            f"reason={validation.reason} readiness_profile={args.readiness_profile}"
        )
        return 0

    if args.readiness_profile in EVIDENCE_READINESS_PROFILES:
        if not args.runtime_evidence_artifact:
            print(
                "Runtime evidence readiness evidence gate failed: "
                f"MISSING_RUNTIME_EVIDENCE_ARTIFACT evidence_profile={args.readiness_profile}"
            )
            return 2
        evidence_valid, evidence_reason = validate_runtime_evidence_artifact(
            Path(args.runtime_evidence_artifact),
            evidence_profile=args.readiness_profile,
        )
        if not evidence_valid:
            print(
                "Runtime evidence readiness evidence gate failed: "
                f"{evidence_reason} evidence_profile={args.readiness_profile}"
            )
            return 1
        if args.readiness_profile == "production":
            _ensure_repo_root_on_path(repo_root)
            from src.app.runtime.orchestration.production_closure_gate import RuntimeProductionClosureGate

            artifact_paths = {}
            if args.p0_e2e_artifact:
                artifact_paths["p0_e2e"] = Path(args.p0_e2e_artifact)
            if args.benchmark_artifact:
                artifact_paths["benchmark"] = Path(args.benchmark_artifact)
            closure_validation = RuntimeProductionClosureGate().validate_artifact_files(
                artifact_paths,
                closure_profile="production",
            )
            if not closure_validation.valid:
                print(
                    "Runtime evidence readiness evidence gate failed: "
                    f"{closure_validation.reason} evidence_profile=production"
                )
                return 2
        print(
            "Runtime evidence readiness evidence gate passed: "
            f"reason={evidence_reason} evidence_profile={args.readiness_profile}"
        )
        return 0

    print(
        "Runtime evidence readiness mock gate passed: "
        f"reason={validation.reason} readiness_profile={args.readiness_profile}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
