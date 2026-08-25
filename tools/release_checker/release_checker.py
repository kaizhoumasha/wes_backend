#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"})
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PROVIDED_PERMISSION_FIELDS = frozenset(
    {"name", "type", "category", "description", "resource", "action", "method", "path"}
)
FRONTEND_LABEL_FIELDS = frozenset(
    {
        "org.wes.release.consumer-openapi.sha256",
        "org.wes.release.required-operations.sha256",
        "org.wes.release.required-permissions.sha256",
        "org.wes.release.frontend-dependencies.sha256",
        "org.wes.release.frontend-recipe.sha256",
    }
)
BACKEND_LABEL_FIELDS = frozenset(
    {
        "org.wes.release.provider-openapi.sha256",
        "org.wes.release.provided-permissions.sha256",
        "org.wes.release.migration-tree.sha256",
        "org.wes.release.backend-dependencies.sha256",
        "org.wes.release.backend-recipe.sha256",
        "org.wes.release.expected-schema-head",
    }
)


class ArtifactValidationError(ValueError):
    pass


class CheckerExecutionError(RuntimeError):
    pass


class CheckerTimeoutError(CheckerExecutionError):
    pass


class ClassificationError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Finding:
    severity: str
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ReleaseArtifacts:
    consumer_openapi: dict[str, Any]
    provider_openapi: dict[str, Any]
    required_operations: tuple[tuple[str, str], ...]
    required_permissions: tuple[str, ...]
    provided_permission_names: frozenset[str]
    frontend_fingerprints: dict[str, str]
    backend_fingerprints: dict[str, str]
    artifact_hashes: dict[str, dict[str, str]]


@dataclass(frozen=True)
class ModeDecision:
    auto_mode: str
    effective_mode: str
    reasons: tuple[str, ...]


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ArtifactValidationError(f"non-finite JSON number is forbidden: {value}")


def _read_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtifactValidationError(f"cannot read required artifact: {path.name}") from exc
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"invalid UTF-8 JSON artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"artifact root must be an object: {path.name}")
    return raw, value


def _require_exact_fields(value: Mapping[str, Any], expected: frozenset[str], subject: str) -> None:
    if set(value) != expected:
        raise ArtifactValidationError(f"{subject} has unknown or missing fields")


def _require_nonempty_string(value: Any, subject: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ArtifactValidationError(f"{subject} must be a non-empty string")
    return value


def _require_sha256(value: Any, subject: str) -> str:
    if not isinstance(value, str) or HEX_SHA256.fullmatch(value) is None:
        raise ArtifactValidationError(f"{subject} must be a lowercase SHA-256")
    return value


def _validate_openapi(value: dict[str, Any], subject: str) -> None:
    _require_nonempty_string(value.get("openapi"), f"{subject}.openapi")
    if not isinstance(value.get("paths"), dict):
        raise ArtifactValidationError(f"{subject}.paths must be an object")
    _validate_internal_refs(value, subject)


def _validate_internal_refs(value: Any, subject: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "$ref" and (not isinstance(nested, str) or not nested.startswith("#/")):
                raise ArtifactValidationError(f"{subject} $ref must be an internal JSON pointer")
            _validate_internal_refs(nested, subject)
    elif isinstance(value, list):
        for nested in value:
            _validate_internal_refs(nested, subject)


def _validate_required_operations(value: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    _require_exact_fields(value, frozenset({"kind", "operations"}), "required operations")
    if value["kind"] != "wes.release.required-operations.v1":
        raise ArtifactValidationError("required operations kind is invalid")
    operations = value["operations"]
    if not isinstance(operations, list):
        raise ArtifactValidationError("required operations must be an array")
    parsed: list[tuple[str, str]] = []
    for item in operations:
        if not isinstance(item, dict) or set(item) != {"method", "path"}:
            raise ArtifactValidationError("required operation has unknown or missing fields")
        method = _require_nonempty_string(item["method"], "required operation method")
        path = _require_nonempty_string(item["path"], "required operation path")
        if method not in HTTP_METHODS:
            raise ArtifactValidationError("required operation method must be uppercase HTTP method")
        if not path.startswith("/"):
            raise ArtifactValidationError("required operation path must start with '/'")
        parsed.append((path, method))
    if parsed != sorted(set(parsed)):
        raise ArtifactValidationError("required operations must be sorted and unique")
    return tuple((method.lower(), path) for path, method in parsed)


def _validate_required_permissions(value: dict[str, Any]) -> tuple[str, ...]:
    _require_exact_fields(value, frozenset({"kind", "permissions"}), "required permissions")
    if value["kind"] != "wes.release.required-permissions.v1":
        raise ArtifactValidationError("required permissions kind is invalid")
    permissions = value["permissions"]
    if not isinstance(permissions, list) or not all(isinstance(item, str) and item for item in permissions):
        raise ArtifactValidationError("required permissions must contain non-empty strings")
    if "*" in permissions:
        raise ArtifactValidationError("required permissions must not contain '*' sentinel")
    if permissions != sorted(set(permissions)):
        raise ArtifactValidationError("required permissions must be sorted and unique")
    return tuple(permissions)


def _validate_provided_permissions(value: dict[str, Any]) -> frozenset[str]:
    _require_exact_fields(value, frozenset({"kind", "permissions"}), "provided permissions")
    if value["kind"] != "wes.release.provided-permissions.v1":
        raise ArtifactValidationError("provided permissions kind is invalid")
    permissions = value["permissions"]
    if not isinstance(permissions, list):
        raise ArtifactValidationError("provided permissions must be an array")
    names: set[str] = set()
    order: list[tuple[str, str, str, str]] = []
    for item in permissions:
        if not isinstance(item, dict) or set(item) != PROVIDED_PERMISSION_FIELDS:
            raise ArtifactValidationError("provided permission fields are invalid")
        if not all(isinstance(field, str) and field for field in item.values()):
            raise ArtifactValidationError("provided permission fields must be non-empty strings")
        name = item["name"]
        if name in names:
            raise ArtifactValidationError(f"duplicate permission name: {name}")
        names.add(name)
        order.append((name, item["type"], item["method"], item["path"]))
    if order != sorted(order):
        raise ArtifactValidationError("provided permissions must use canonical order")
    return frozenset(names)


def _validate_labels(labels: Mapping[str, str], expected: frozenset[str], subject: str) -> dict[str, str]:
    _require_exact_fields(labels, expected, f"{subject} OCI label fields")
    result: dict[str, str] = {}
    for key, value in labels.items():
        if key.endswith("expected-schema-head"):
            result[key] = _require_nonempty_string(value, key)
        else:
            result[key] = _require_sha256(value, key)
    return result


def _check_raw_hash(raw: bytes, label: str, artifact_name: str) -> str:
    actual = hashlib.sha256(raw).hexdigest()
    if actual != label:
        raise ArtifactValidationError(f"{artifact_name} raw SHA-256 does not match OCI label")
    return actual


def load_release_artifacts(
    frontend_dir: Path,
    backend_dir: Path,
    frontend_labels: Mapping[str, str],
    backend_labels: Mapping[str, str],
) -> ReleaseArtifacts:
    frontend_label_values = _validate_labels(frontend_labels, FRONTEND_LABEL_FIELDS, "frontend")
    backend_label_values = _validate_labels(backend_labels, BACKEND_LABEL_FIELDS, "backend")

    consumer_raw, consumer_openapi = _read_json(frontend_dir / "consumer-openapi.json")
    operations_raw, operations_document = _read_json(frontend_dir / "required-operations.json")
    required_permissions_raw, required_permissions_document = _read_json(frontend_dir / "required-permissions.json")
    provider_raw, provider_openapi = _read_json(backend_dir / "provider-openapi.json")
    provided_permissions_raw, provided_permissions_document = _read_json(backend_dir / "provided-permissions.json")

    _validate_openapi(consumer_openapi, "consumer OpenAPI")
    _validate_openapi(provider_openapi, "provider OpenAPI")
    required_operations = _validate_required_operations(operations_document)
    required_permissions = _validate_required_permissions(required_permissions_document)
    provided_permission_names = _validate_provided_permissions(provided_permissions_document)
    frontend_fingerprints = {
        "consumer_openapi_sha256": frontend_label_values["org.wes.release.consumer-openapi.sha256"],
        "required_operations_sha256": frontend_label_values["org.wes.release.required-operations.sha256"],
        "required_permissions_sha256": frontend_label_values["org.wes.release.required-permissions.sha256"],
        "dependencies_sha256": frontend_label_values["org.wes.release.frontend-dependencies.sha256"],
        "recipe_sha256": frontend_label_values["org.wes.release.frontend-recipe.sha256"],
    }
    backend_fingerprints = {
        "provider_openapi_sha256": backend_label_values["org.wes.release.provider-openapi.sha256"],
        "provided_permissions_sha256": backend_label_values["org.wes.release.provided-permissions.sha256"],
        "migration_tree_sha256": backend_label_values["org.wes.release.migration-tree.sha256"],
        "dependencies_sha256": backend_label_values["org.wes.release.backend-dependencies.sha256"],
        "recipe_sha256": backend_label_values["org.wes.release.backend-recipe.sha256"],
        "expected_schema_head": backend_label_values["org.wes.release.expected-schema-head"],
    }
    for method, path in required_operations:
        path_item = consumer_openapi["paths"].get(path)
        if not isinstance(path_item, dict) or method not in path_item:
            raise ArtifactValidationError(f"required operation {method.upper()} {path} is absent from consumer OpenAPI")

    frontend_hashes = {
        "consumer_openapi": _check_raw_hash(
            consumer_raw,
            frontend_label_values["org.wes.release.consumer-openapi.sha256"],
            "consumer-openapi.json",
        ),
        "required_operations": _check_raw_hash(
            operations_raw,
            frontend_label_values["org.wes.release.required-operations.sha256"],
            "required-operations.json",
        ),
        "required_permissions": _check_raw_hash(
            required_permissions_raw,
            frontend_label_values["org.wes.release.required-permissions.sha256"],
            "required-permissions.json",
        ),
    }
    backend_hashes = {
        "provider_openapi": _check_raw_hash(
            provider_raw,
            backend_label_values["org.wes.release.provider-openapi.sha256"],
            "provider-openapi.json",
        ),
        "provided_permissions": _check_raw_hash(
            provided_permissions_raw,
            backend_label_values["org.wes.release.provided-permissions.sha256"],
            "provided-permissions.json",
        ),
    }
    return ReleaseArtifacts(
        consumer_openapi=consumer_openapi,
        provider_openapi=provider_openapi,
        required_operations=required_operations,
        required_permissions=required_permissions,
        provided_permission_names=provided_permission_names,
        frontend_fingerprints=frontend_fingerprints,
        backend_fingerprints=backend_fingerprints,
        artifact_hashes={"frontend": frontend_hashes, "backend": backend_hashes},
    )


def check_required_permissions(
    required_permissions: Sequence[str], provided_permission_names: frozenset[str]
) -> tuple[Finding, ...]:
    return tuple(
        Finding(
            severity="ERR",
            code="required-permission-missing",
            location=f"permission:{name}",
            message="required permission is not provided by the selected backend",
        )
        for name in sorted(set(required_permissions) - provided_permission_names)
    )


def project_selected_operations(
    spec: dict[str, Any], selected_operations: frozenset[tuple[str, str]]
) -> dict[str, Any]:
    projected = deepcopy(spec)
    selected_paths = {path for _, path in selected_operations}
    for path, path_item in list(projected["paths"].items()):
        if not isinstance(path_item, dict):
            raise ArtifactValidationError(f"OpenAPI Path Item must be an object: {path}")
        if path in selected_paths and "$ref" in path_item:
            raise ArtifactValidationError(
                f"selected Path Item {path!r} contains unsupported external or internal $ref {path_item['$ref']!r}"
            )
        for method in (item.lower() for item in HTTP_METHODS):
            if method in path_item and (method, path) not in selected_operations:
                del path_item[method]
        if not {item.lower() for item in HTTP_METHODS}.intersection(path_item):
            del projected["paths"][path]
    return projected


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _parse_oasdiff_findings(stdout: str) -> tuple[Finding, ...]:
    try:
        raw_findings = json.loads(
            stdout,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, ArtifactValidationError) as exc:
        raise CheckerExecutionError("oasdiff returned an invalid result") from exc
    if not isinstance(raw_findings, list):
        raise CheckerExecutionError("oasdiff returned an invalid result")
    findings: list[Finding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            raise CheckerExecutionError("oasdiff returned an invalid result")
        code = item.get("id")
        message = item.get("text")
        level = item.get("level")
        method = item.get("operation", "")
        path = item.get("path", "")
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(message, str)
            or not message
            or not isinstance(level, int)
            or not isinstance(method, str)
            or not isinstance(path, str)
        ):
            raise CheckerExecutionError("oasdiff returned an invalid result")
        location = " ".join(part for part in (method, path) if part) or "openapi"
        findings.append(
            Finding(
                severity="ERR" if level >= 3 else "WARN",
                code=code,
                location=location,
                message=message,
            )
        )
    return tuple(sorted(findings))


def run_oasdiff(
    consumer_openapi: dict[str, Any],
    provider_openapi: dict[str, Any],
    required_operations: frozenset[tuple[str, str]],
    oasdiff_bin: Path,
    *,
    timeout_seconds: float = 60,
) -> tuple[Finding, ...]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    with tempfile.TemporaryDirectory(prefix="wes-release-checker-") as directory:
        work_dir = Path(directory)
        consumer_path = work_dir / "consumer-baseline.json"
        provider_path = work_dir / "selected-provider.json"
        consumer_path.write_bytes(
            canonical_json_bytes(project_selected_operations(consumer_openapi, required_operations))
        )
        provider_path.write_bytes(
            canonical_json_bytes(project_selected_operations(provider_openapi, required_operations))
        )
        try:
            completed = subprocess.run(  # noqa: S603 - the checker image pins this executable
                [
                    str(oasdiff_bin),
                    "breaking",
                    str(consumer_path),
                    str(provider_path),
                    "--allow-external-refs=false",
                    "--format=json",
                    "--fail-on=WARN",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=min(timeout_seconds, 60),
            )
        except subprocess.TimeoutExpired as exc:
            raise CheckerTimeoutError("oasdiff exceeded configured timeout") from exc
        except OSError as exc:
            raise CheckerExecutionError("oasdiff execution failed") from exc
    if completed.returncode not in {0, 1} or completed.stderr:
        raise CheckerExecutionError("oasdiff execution failed")
    findings = _parse_oasdiff_findings(completed.stdout)
    if completed.returncode == 0 and findings:
        raise CheckerExecutionError("oasdiff returned inconsistent status")
    if completed.returncode == 1 and not findings:
        raise CheckerExecutionError("oasdiff returned inconsistent status")
    return findings


FRONTEND_FACT_FIELDS = frozenset(
    {
        "consumer_openapi_sha256",
        "required_operations_sha256",
        "required_permissions_sha256",
        "dependencies_sha256",
        "recipe_sha256",
    }
)
BACKEND_FACT_FIELDS = frozenset(
    {
        "provider_openapi_sha256",
        "provided_permissions_sha256",
        "migration_tree_sha256",
        "dependencies_sha256",
        "recipe_sha256",
        "expected_schema_head",
    }
)
FULL_REASON_NAMES = {
    "frontend": {
        "consumer_openapi_sha256": "frontend.consumer-openapi.changed",
        "required_operations_sha256": "frontend.required-operations.changed",
        "required_permissions_sha256": "frontend.required-permissions.changed",
        "dependencies_sha256": "frontend.dependencies.changed",
        "recipe_sha256": "frontend.recipe.changed",
    },
    "backend": {
        "provider_openapi_sha256": "backend.provider-openapi.changed",
        "provided_permissions_sha256": "backend.provided-permissions.changed",
        "migration_tree_sha256": "backend.migration-tree.changed",
        "dependencies_sha256": "backend.dependencies.changed",
        "recipe_sha256": "backend.recipe.changed",
        "expected_schema_head": "backend.expected-schema-head.changed",
    },
}


def _validate_image_facts(value: Any, *, frontend: bool) -> dict[str, str]:
    fields = FRONTEND_FACT_FIELDS if frontend else BACKEND_FACT_FIELDS
    subject = "frontend" if frontend else "backend"
    if not isinstance(value, dict) or set(value) != fields:
        raise ArtifactValidationError(f"{subject} fingerprint fields are invalid")
    result: dict[str, str] = {}
    for key in fields - {"expected_schema_head"}:
        result[key] = _require_sha256(value[key], f"{subject}.{key}")
    if not frontend:
        result["expected_schema_head"] = _require_nonempty_string(
            value["expected_schema_head"], "backend.expected_schema_head"
        )
    return result


def _validate_input_set(value: Any, subject: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"kind", "files"}:
        raise ArtifactValidationError(f"{subject} input-set fields are invalid")
    if value["kind"] != "wes.release.input-set.v1" or not isinstance(value["files"], list):
        raise ArtifactValidationError(f"{subject} input-set is invalid")
    files: list[dict[str, str]] = []
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ArtifactValidationError(f"{subject} input-set file fields are invalid")
        path = _require_nonempty_string(item["path"], f"{subject} input path")
        posix_path = PurePosixPath(path)
        if (
            path != posix_path.as_posix()
            or posix_path.is_absolute()
            or ".." in posix_path.parts
            or path in {"", "."}
            or "\\" in path
        ):
            raise ArtifactValidationError(f"{subject} input path must be repository-relative POSIX")
        files.append({"path": path, "sha256": _require_sha256(item["sha256"], f"{subject}.{path}")})
    ordering = [item["path"] for item in files]
    if ordering != sorted(set(ordering)):
        raise ArtifactValidationError(f"{subject} input-set files must be sorted and unique")
    return {"kind": "wes.release.input-set.v1", "files": files}


def _validate_current_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"kind", "frontend", "backend", "deploy", "runtime"}:
        raise ArtifactValidationError("current fingerprint fields are invalid")
    if value["kind"] != "wes.release.current-fingerprints.v1":
        raise ArtifactValidationError("current fingerprint kind is invalid")
    return {
        "frontend": _validate_image_facts(value["frontend"], frontend=True),
        "backend": _validate_image_facts(value["backend"], frontend=False),
        "deploy": _validate_input_set(value["deploy"], "current deploy"),
        "runtime": _validate_input_set(value["runtime"], "current runtime"),
    }


def _validate_effective_facts(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"kind", "deploy", "runtime", "database"}:
        raise ArtifactValidationError("effective fact fields are invalid")
    if value["kind"] != "wes.release.effective-facts.v1":
        raise ArtifactValidationError("effective fact kind is invalid")
    database = value["database"]
    if not isinstance(database, dict) or set(database) != {"current_heads", "relation_to_candidate"}:
        raise ArtifactValidationError("database fact fields are invalid")
    heads = database["current_heads"]
    relation = database["relation_to_candidate"]
    if not isinstance(heads, list) or not all(isinstance(item, str) and item for item in heads):
        raise ArtifactValidationError("database current_heads must contain non-empty strings")
    if relation not in {"equal", "ancestor", "unknown", "diverged", "downgrade"}:
        raise ArtifactValidationError("database relation enum is invalid")
    return {
        "deploy": _validate_input_set(value["deploy"], "effective deploy"),
        "runtime": _validate_input_set(value["runtime"], "effective runtime"),
        "database": {"current_heads": heads, "relation_to_candidate": relation},
    }


def classify_release_mode(
    candidate_frontend: Mapping[str, str],
    candidate_backend: Mapping[str, str],
    current_evidence: Mapping[str, Any] | None,
    effective_facts: Mapping[str, Any],
    *,
    force_full: bool = False,
) -> ModeDecision:
    candidate = {
        "frontend": _validate_image_facts(dict(candidate_frontend), frontend=True),
        "backend": _validate_image_facts(dict(candidate_backend), frontend=False),
    }
    effective = _validate_effective_facts(dict(effective_facts))
    reasons: set[str] = set()
    if current_evidence is None:
        reasons.add("current-evidence.missing")
    else:
        current = _validate_current_evidence(dict(current_evidence))
        for side in ("frontend", "backend"):
            for key, reason in FULL_REASON_NAMES[side].items():
                if current[side][key] != candidate[side][key]:
                    reasons.add(reason)
        if current["deploy"] != effective["deploy"]:
            reasons.add("deploy.inputs.changed")
        if current["runtime"] != effective["runtime"]:
            reasons.add("runtime.inputs.changed")

    database = effective["database"]
    if len(database["current_heads"]) != 1:
        raise ClassificationError("database must have exactly one current head")
    current_head = database["current_heads"][0]
    expected_head = candidate["backend"]["expected_schema_head"]
    relation = database["relation_to_candidate"]
    if relation == "equal":
        if current_head != expected_head:
            raise ClassificationError("equal database relation does not match candidate expected head")
    elif relation == "ancestor":
        if current_head == expected_head:
            raise ClassificationError("ancestor database relation cannot use the candidate expected head")
        reasons.add("database.forward-migration")
    else:
        raise ClassificationError(f"database relation {relation!r} is not deployable")

    auto_mode = "FULL" if reasons else "FAST"
    effective_mode = "FULL" if force_full or auto_mode == "FULL" else "FAST"
    if force_full:
        reasons.add("operator.force-full")
    return ModeDecision(auto_mode=auto_mode, effective_mode=effective_mode, reasons=tuple(sorted(reasons)))


REPORT_FIELDS = frozenset(
    {
        "kind",
        "release_id",
        "deploy_scope",
        "candidate_digests",
        "current_digests",
        "checker_digest",
        "artifact_hashes",
        "auto_mode",
        "effective_mode",
        "mode_reasons",
        "compatibility",
        "approval",
        "pre_cutover_state",
    }
)
OCI_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _require_digest(value: Any, subject: str) -> str:
    if not isinstance(value, str) or OCI_DIGEST.fullmatch(value) is None:
        raise ArtifactValidationError(f"{subject} must be an immutable sha256 digest")
    return value


def _validate_digest_maps(
    deploy_scope: str, candidate_digests: Any, current_digests: Any
) -> tuple[dict[str, str], dict[str, str]]:
    expected_candidate = {
        "FRONTEND": {"frontend"},
        "BACKEND": {"backend"},
        "BOTH": {"frontend", "backend"},
    }[deploy_scope]
    if not isinstance(candidate_digests, dict) or set(candidate_digests) != expected_candidate:
        raise ArtifactValidationError("candidate digest scope is invalid")
    if not isinstance(current_digests, dict) or set(current_digests) != {"frontend", "backend"}:
        raise ArtifactValidationError("current digest scope is invalid")
    return (
        {key: _require_digest(value, f"candidate {key}") for key, value in candidate_digests.items()},
        {key: _require_digest(value, f"current {key}") for key, value in current_digests.items()},
    )


def _selected_pair(candidate_digests: Mapping[str, str], current_digests: Mapping[str, str]) -> tuple[str, str]:
    return (
        candidate_digests.get("frontend", current_digests["frontend"]),
        candidate_digests.get("backend", current_digests["backend"]),
    )


def _finding_dict(finding: Finding) -> dict[str, str]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "location": finding.location,
        "message": finding.message,
    }


def build_compatibility_report(
    *,
    release_id: str,
    deploy_scope: str,
    candidate_digests: Mapping[str, str],
    current_digests: Mapping[str, str],
    checker_digest: str,
    artifact_hashes: Mapping[str, Mapping[str, str]],
    mode: ModeDecision,
    findings: Sequence[Finding],
    approval_reason: str | None = None,
) -> dict[str, Any]:
    _require_nonempty_string(release_id, "release_id")
    if deploy_scope not in {"FRONTEND", "BACKEND", "BOTH"}:
        raise ArtifactValidationError("deploy_scope enum is invalid")
    candidate, current = _validate_digest_maps(deploy_scope, dict(candidate_digests), dict(current_digests))
    checker = _require_digest(checker_digest, "checker digest")
    sorted_findings = sorted(findings)
    finding_documents = [_finding_dict(item) for item in sorted_findings]
    diff_hash = hashlib.sha256(_canonical_json_without_lf(finding_documents)).hexdigest()
    status = (
        "ERR" if any(item.severity == "ERR" for item in sorted_findings) else ("WARN" if sorted_findings else "PASS")
    )
    approval = None
    if status == "WARN" and approval_reason is not None:
        reason = _require_nonempty_string(approval_reason, "approval reason")
        selected_frontend, selected_backend = _selected_pair(candidate, current)
        approval = {
            "frontend_digest": selected_frontend,
            "backend_digest": selected_backend,
            "checker_digest": checker,
            "diff_hash": diff_hash,
            "reason": reason,
        }
    ready = status == "PASS" or (status == "WARN" and approval is not None)
    report: dict[str, Any] = {
        "kind": "wes.release.compatibility-report.v1",
        "release_id": release_id,
        "deploy_scope": deploy_scope,
        "candidate_digests": candidate,
        "current_digests": current,
        "checker_digest": checker,
        "artifact_hashes": {side: dict(values) for side, values in artifact_hashes.items()},
        "auto_mode": mode.auto_mode,
        "effective_mode": mode.effective_mode,
        "mode_reasons": list(mode.reasons),
        "compatibility": {"status": status, "diff_hash": diff_hash, "findings": finding_documents},
        "approval": approval,
        "pre_cutover_state": "READY" if ready else "PRE_CUTOVER_ABORTED",
    }
    validate_compatibility_report(report)
    return report


def _canonical_json_without_lf(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def validate_compatibility_report(report: Mapping[str, Any]) -> None:  # noqa: PLR0912
    if set(report) != REPORT_FIELDS:
        raise ArtifactValidationError("compatibility report fields are invalid")
    if report["kind"] != "wes.release.compatibility-report.v1":
        raise ArtifactValidationError("compatibility report kind is invalid")
    _require_nonempty_string(report["release_id"], "release_id")
    deploy_scope = report["deploy_scope"]
    if deploy_scope not in {"FRONTEND", "BACKEND", "BOTH"}:
        raise ArtifactValidationError("deploy_scope enum is invalid")
    candidate, current = _validate_digest_maps(deploy_scope, report["candidate_digests"], report["current_digests"])
    checker = _require_digest(report["checker_digest"], "checker digest")
    artifact_hashes = report["artifact_hashes"]
    if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != {"frontend", "backend"}:
        raise ArtifactValidationError("artifact hash sides are invalid")
    expected_hash_fields = {
        "frontend": {"consumer_openapi", "required_operations", "required_permissions"},
        "backend": {"provider_openapi", "provided_permissions"},
    }
    for side, fields in expected_hash_fields.items():
        values = artifact_hashes[side]
        if not isinstance(values, dict) or set(values) != fields:
            raise ArtifactValidationError(f"{side} artifact hash fields are invalid")
        for key, value in values.items():
            _require_sha256(value, f"{side}.{key}")
    if report["auto_mode"] not in {"FAST", "FULL"} or report["effective_mode"] not in {"FAST", "FULL"}:
        raise ArtifactValidationError("release mode enum is invalid")
    if report["auto_mode"] == "FULL" and report["effective_mode"] != "FULL":
        raise ArtifactValidationError("effective mode cannot downgrade automatic FULL")
    reasons = report["mode_reasons"]
    if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
        raise ArtifactValidationError("mode reasons are invalid")
    if reasons != sorted(set(reasons)):
        raise ArtifactValidationError("mode reasons must be sorted and unique")
    compatibility = report["compatibility"]
    if not isinstance(compatibility, dict) or set(compatibility) != {"status", "diff_hash", "findings"}:
        raise ArtifactValidationError("compatibility fields are invalid")
    if compatibility["status"] not in {"PASS", "WARN", "ERR"}:
        raise ArtifactValidationError("compatibility status enum is invalid")
    _require_sha256(compatibility["diff_hash"], "compatibility.diff_hash")
    finding_documents = compatibility["findings"]
    if not isinstance(finding_documents, list):
        raise ArtifactValidationError("compatibility findings must be an array")
    finding_order: list[tuple[str, str, str, str]] = []
    for item in finding_documents:
        if not isinstance(item, dict) or set(item) != {"code", "severity", "location", "message"}:
            raise ArtifactValidationError("compatibility finding fields are invalid")
        if item["severity"] not in {"WARN", "ERR"}:
            raise ArtifactValidationError("compatibility finding severity is invalid")
        for field in ("code", "location", "message"):
            _require_nonempty_string(item[field], f"finding.{field}")
        finding_order.append((item["severity"], item["code"], item["location"], item["message"]))
    if finding_order != sorted(finding_order):
        raise ArtifactValidationError("compatibility findings must be sorted")
    expected_diff_hash = hashlib.sha256(_canonical_json_without_lf(finding_documents)).hexdigest()
    if compatibility["diff_hash"] != expected_diff_hash:
        raise ArtifactValidationError("compatibility diff hash is invalid")
    expected_status = (
        "ERR" if any(item[0] == "ERR" for item in finding_order) else ("WARN" if finding_order else "PASS")
    )
    if compatibility["status"] != expected_status:
        raise ArtifactValidationError("compatibility status does not match findings")
    approval = report["approval"]
    if approval is not None:
        if not isinstance(approval, dict) or set(approval) != {
            "frontend_digest",
            "backend_digest",
            "checker_digest",
            "diff_hash",
            "reason",
        }:
            raise ArtifactValidationError("approval fields are invalid")
        selected_frontend, selected_backend = _selected_pair(candidate, current)
        if (
            compatibility["status"] != "WARN"
            or approval["frontend_digest"] != selected_frontend
            or approval["backend_digest"] != selected_backend
            or approval["checker_digest"] != checker
            or approval["diff_hash"] != compatibility["diff_hash"]
        ):
            raise ArtifactValidationError("approval binding is invalid")
        _require_nonempty_string(approval["reason"], "approval reason")
    state = report["pre_cutover_state"]
    if state not in {"READY", "PRE_CUTOVER_ABORTED"}:
        raise ArtifactValidationError("pre_cutover_state enum is invalid")
    expected_ready = compatibility["status"] == "PASS" or (compatibility["status"] == "WARN" and approval is not None)
    if (state == "READY") != expected_ready:
        raise ArtifactValidationError("pre_cutover_state does not match compatibility")


def _read_json_object(path: Path) -> dict[str, Any]:
    return _read_json(path)[1]


def _write_report_atomically(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_json_bytes(report)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check one selected frontend/backend release pair")
    parser.add_argument("--frontend-dir", required=True, type=Path)
    parser.add_argument("--backend-dir", required=True, type=Path)
    parser.add_argument("--frontend-labels", required=True, type=Path)
    parser.add_argument("--backend-labels", required=True, type=Path)
    parser.add_argument("--current-fingerprints", type=Path)
    parser.add_argument("--effective-facts", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--deploy-scope", required=True, choices=("FRONTEND", "BACKEND", "BOTH"))
    parser.add_argument("--candidate-digests", required=True, type=Path)
    parser.add_argument("--current-digests", required=True, type=Path)
    parser.add_argument("--checker-digest", required=True)
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--warn-approval-reason")
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    oasdiff_bin: Path | None = None,
    timeout_seconds: float = 60,
) -> int:
    args = _build_argument_parser().parse_args(argv)
    artifacts = load_release_artifacts(
        args.frontend_dir,
        args.backend_dir,
        _read_json_object(args.frontend_labels),
        _read_json_object(args.backend_labels),
    )
    current_evidence = None
    if args.current_fingerprints is not None:
        try:
            current_evidence = _read_json_object(args.current_fingerprints)
            _validate_current_evidence(current_evidence)
        except (ArtifactValidationError, OSError):
            current_evidence = None
    findings: list[Finding] = []
    try:
        mode = classify_release_mode(
            artifacts.frontend_fingerprints,
            artifacts.backend_fingerprints,
            current_evidence,
            _read_json_object(args.effective_facts),
            force_full=args.force_full,
        )
    except ClassificationError:
        mode = ModeDecision(auto_mode="FULL", effective_mode="FULL", reasons=("database.state-invalid",))
        findings.append(
            Finding(
                severity="ERR",
                code="database-state-not-deployable",
                location="database",
                message="Database state is not an equal or known-ancestor release state",
            )
        )
    findings.extend(check_required_permissions(artifacts.required_permissions, artifacts.provided_permission_names))
    binary = oasdiff_bin or Path(os.environ.get("OASDIFF_BIN", "/usr/local/bin/oasdiff"))
    try:
        findings.extend(
            run_oasdiff(
                artifacts.consumer_openapi,
                artifacts.provider_openapi,
                frozenset(artifacts.required_operations),
                binary,
                timeout_seconds=timeout_seconds,
            )
        )
    except CheckerTimeoutError:
        findings.append(
            Finding(
                severity="ERR",
                code="checker-oasdiff-timeout",
                location="checker",
                message="OpenAPI compatibility check exceeded its hard timeout",
            )
        )
    except (CheckerExecutionError, ArtifactValidationError):
        findings.append(
            Finding(
                severity="ERR",
                code="checker-oasdiff-failed",
                location="checker",
                message="OpenAPI compatibility check failed before cutover",
            )
        )
    report = build_compatibility_report(
        release_id=args.release_id,
        deploy_scope=args.deploy_scope,
        candidate_digests=_read_json_object(args.candidate_digests),
        current_digests=_read_json_object(args.current_digests),
        checker_digest=args.checker_digest,
        artifact_hashes=artifacts.artifact_hashes,
        mode=mode,
        findings=findings,
        approval_reason=args.warn_approval_reason,
    )
    _write_report_atomically(args.output, report)
    return 0 if report["pre_cutover_state"] == "READY" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArtifactValidationError, ClassificationError, OSError, ValueError):
        print("release checker aborted before a valid report could be produced", file=sys.stderr)
        raise SystemExit(2) from None
