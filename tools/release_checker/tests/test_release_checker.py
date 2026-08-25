from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from tools.release_checker.release_checker import (
    ArtifactValidationError,
    CheckerExecutionError,
    CheckerTimeoutError,
    ClassificationError,
    Finding,
    build_compatibility_report,
    check_required_permissions,
    classify_release_mode,
    load_release_artifacts,
    main,
    project_selected_operations,
    run_oasdiff,
    validate_compatibility_report,
)

FIXTURES = Path(__file__).parent / "fixtures"
SHA256_A = "a" * 64
SHA256_B = "b" * 64


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_json(path: Path, value: Any) -> str:
    raw = _json_bytes(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _valid_release_inputs(tmp_path: Path) -> tuple[Path, Path, dict[str, str], dict[str, str]]:
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()

    consumer_openapi = (FIXTURES / "consumer-used-operation.json").read_bytes()
    provider_openapi = (FIXTURES / "provider-compatible-unused-change.json").read_bytes()
    (frontend / "consumer-openapi.json").write_bytes(consumer_openapi)
    (backend / "provider-openapi.json").write_bytes(provider_openapi)
    required_operations_sha = _write_json(
        frontend / "required-operations.json",
        {
            "kind": "wes.release.required-operations.v1",
            "operations": [{"method": "GET", "path": "/orders/{order_id}"}],
        },
    )
    required_permissions_sha = _write_json(
        frontend / "required-permissions.json",
        {"kind": "wes.release.required-permissions.v1", "permissions": ["user_api:biz:device:view"]},
    )
    provided_permissions_sha = _write_json(
        backend / "provided-permissions.json",
        {
            "kind": "wes.release.provided-permissions.v1",
            "permissions": [
                {
                    "action": "view",
                    "category": "biz",
                    "description": "查看设备",
                    "method": "GET",
                    "name": "user_api:biz:device:view",
                    "path": "/devices",
                    "resource": "device",
                    "type": "user_api",
                }
            ],
        },
    )
    consumer_openapi_sha = hashlib.sha256(consumer_openapi).hexdigest()
    provider_openapi_sha = hashlib.sha256(provider_openapi).hexdigest()
    frontend_labels = {
        "org.wes.release.consumer-openapi.sha256": consumer_openapi_sha,
        "org.wes.release.frontend-dependencies.sha256": SHA256_A,
        "org.wes.release.frontend-recipe.sha256": SHA256_B,
        "org.wes.release.required-operations.sha256": required_operations_sha,
        "org.wes.release.required-permissions.sha256": required_permissions_sha,
    }
    backend_labels = {
        "org.wes.release.backend-dependencies.sha256": SHA256_A,
        "org.wes.release.backend-recipe.sha256": SHA256_A,
        "org.wes.release.expected-schema-head": "d68e6be4006e",
        "org.wes.release.migration-tree.sha256": SHA256_B,
        "org.wes.release.provided-permissions.sha256": provided_permissions_sha,
        "org.wes.release.provider-openapi.sha256": provider_openapi_sha,
    }
    return frontend, backend, frontend_labels, backend_labels


def test_load_release_artifacts_validates_raw_hashes_and_exact_labels(tmp_path: Path) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)

    artifacts = load_release_artifacts(frontend, backend, frontend_labels, backend_labels)

    assert artifacts.required_operations == (("get", "/orders/{order_id}"),)
    assert artifacts.required_permissions == ("user_api:biz:device:view",)
    assert artifacts.provided_permission_names == frozenset({"user_api:biz:device:view"})
    assert artifacts.backend_fingerprints["expected_schema_head"] == "d68e6be4006e"


@pytest.mark.parametrize(
    ("file_name", "mutator", "message"),
    [
        (
            "required-operations.json",
            lambda value: value.update({"unknown": True}),
            "unknown or missing fields",
        ),
        (
            "required-operations.json",
            lambda value: value.update({"kind": "wrong"}),
            "kind",
        ),
        (
            "required-operations.json",
            lambda value: value["operations"][0].update({"method": "get"}),
            "uppercase",
        ),
        (
            "required-operations.json",
            lambda value: value["operations"].append(value["operations"][0].copy()),
            "sorted and unique",
        ),
        (
            "required-permissions.json",
            lambda value: value["permissions"].extend(["aaa", "aaa"]),
            "sorted and unique",
        ),
        (
            "required-permissions.json",
            lambda value: value["permissions"].append("*"),
            "must not contain",
        ),
        (
            "provided-permissions.json",
            lambda value: value["permissions"][0].update({"unknown": "x"}),
            "permission fields",
        ),
        (
            "provided-permissions.json",
            lambda value: value["permissions"][0].update({"description": ""}),
            "non-empty strings",
        ),
        (
            "provided-permissions.json",
            lambda value: value["permissions"].append(value["permissions"][0].copy()),
            "duplicate permission name",
        ),
    ],
)
def test_load_release_artifacts_rejects_noncanonical_contracts(
    tmp_path: Path, file_name: str, mutator: Any, message: str
) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    path = (frontend if (frontend / file_name).exists() else backend) / file_name
    value = json.loads(path.read_text())
    mutator(value)
    path.write_bytes(_json_bytes(value))

    with pytest.raises(ArtifactValidationError, match=message):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


def test_load_release_artifacts_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    (frontend / "required-permissions.json").write_text(
        '{"kind":"wes.release.required-permissions.v1","kind":"wes.release.required-permissions.v1",'
        '"permissions":["user_api:biz:device:view"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ArtifactValidationError, match="duplicate JSON key"):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


def test_load_release_artifacts_does_not_require_ci_only_fingerprint_files(tmp_path: Path) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)

    assert sorted(path.name for path in frontend.iterdir()) == [
        "consumer-openapi.json",
        "required-operations.json",
        "required-permissions.json",
    ]
    assert sorted(path.name for path in backend.iterdir()) == [
        "provided-permissions.json",
        "provider-openapi.json",
    ]
    load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


def test_load_release_artifacts_rejects_nonfinite_json_numbers(tmp_path: Path) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    (frontend / "consumer-openapi.json").write_text(
        '{"openapi":"3.0.3","paths":{},"x-invalid":NaN}\n', encoding="utf-8"
    )

    with pytest.raises(ArtifactValidationError, match="non-finite JSON number"):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


def test_load_release_artifacts_rejects_raw_byte_sha_or_label_mismatch(tmp_path: Path) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    frontend_labels["org.wes.release.required-permissions.sha256"] = SHA256_A

    with pytest.raises(ArtifactValidationError, match=r"required-permissions\.json raw SHA-256"):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


def test_load_release_artifacts_rejects_missing_or_unknown_release_label(tmp_path: Path) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    frontend_labels.pop("org.wes.release.frontend-recipe.sha256")
    backend_labels["org.wes.release.unknown"] = SHA256_A

    with pytest.raises(ArtifactValidationError, match="frontend OCI label fields"):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


def test_load_release_artifacts_rejects_required_operation_absent_from_consumer_openapi(tmp_path: Path) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    operations_path = frontend / "required-operations.json"
    new_sha = _write_json(
        operations_path,
        {"kind": "wes.release.required-operations.v1", "operations": [{"method": "GET", "path": "/absent"}]},
    )
    frontend_labels["org.wes.release.required-operations.sha256"] = new_sha

    with pytest.raises(ArtifactValidationError, match="absent from consumer OpenAPI"):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


@pytest.mark.parametrize(
    ("side", "mutation"),
    [
        (
            "consumer",
            lambda spec: spec["paths"]["/unused"].update({"$ref": "https://example.invalid/consumer-unused-path.json"}),
        ),
        (
            "provider",
            lambda spec: spec["paths"]["/unused-new"]["get"]["responses"]["204"].update(
                {"$ref": "external-provider-unused-operation.json"}
            ),
        ),
        (
            "consumer",
            lambda spec: spec["components"]["schemas"].update(
                {"UnusedExternal": {"$ref": "../consumer-components.json"}}
            ),
        ),
        (
            "provider",
            lambda spec: spec["components"]["schemas"].update(
                {"UnusedExternal": {"$ref": "https://example.invalid/provider-components.json"}}
            ),
        ),
    ],
    ids=(
        "consumer-unselected-path",
        "provider-unselected-operation",
        "consumer-components",
        "provider-components",
    ),
)
def test_load_release_artifacts_rejects_external_refs_anywhere_in_either_openapi(
    tmp_path: Path, side: str, mutation: Any
) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    path = (frontend / "consumer-openapi.json") if side == "consumer" else (backend / "provider-openapi.json")
    spec = json.loads(path.read_text())
    mutation(spec)
    new_sha = _write_json(path, spec)
    labels = frontend_labels if side == "consumer" else backend_labels
    label = (
        "org.wes.release.consumer-openapi.sha256" if side == "consumer" else "org.wes.release.provider-openapi.sha256"
    )
    labels[label] = new_sha

    with pytest.raises(ArtifactValidationError, match=r"\$ref must be an internal JSON pointer"):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


@pytest.mark.parametrize("side", ["consumer", "provider"])
def test_load_release_artifacts_rejects_non_string_refs(tmp_path: Path, side: str) -> None:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    path = (frontend / "consumer-openapi.json") if side == "consumer" else (backend / "provider-openapi.json")
    spec = json.loads(path.read_text())
    spec["components"]["schemas"]["InvalidRef"] = {"$ref": 42}
    new_sha = _write_json(path, spec)
    labels = frontend_labels if side == "consumer" else backend_labels
    label = (
        "org.wes.release.consumer-openapi.sha256" if side == "consumer" else "org.wes.release.provider-openapi.sha256"
    )
    labels[label] = new_sha

    with pytest.raises(ArtifactValidationError, match=r"\$ref must be an internal JSON pointer"):
        load_release_artifacts(frontend, backend, frontend_labels, backend_labels)


def test_required_permissions_are_directional_subset() -> None:
    assert check_required_permissions(("a",), frozenset({"a", "unused"})) == ()

    findings = check_required_permissions(("a", "missing"), frozenset({"a"}))

    assert [(item.code, item.severity, item.location) for item in findings] == [
        ("required-permission-missing", "ERR", "permission:missing")
    ]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env python3\n{body}", encoding="utf-8")
    path.chmod(0o755)


def test_project_selected_operations_reuses_method_aware_full_set_projection() -> None:
    source = json.loads((FIXTURES / "consumer-used-operation.json").read_text())

    projected = project_selected_operations(source, frozenset({("get", "/orders/{order_id}")}))

    assert set(projected["paths"]) == {"/orders/{order_id}"}
    assert set(projected["paths"]["/orders/{order_id}"]) == {"get"}
    assert projected["components"] == source["components"]


def test_run_oasdiff_invokes_once_and_normalizes_deterministic_findings(tmp_path: Path) -> None:
    invocation_log = tmp_path / "invocations"
    binary = tmp_path / "oasdiff"
    _write_executable(
        binary,
        "import json, os, sys\n"
        "with open(os.environ['INVOCATION_LOG'], 'a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps(["
        "{'id':'response-optional-property-updated','text':'optional response property changed','level':2,"
        "'operation':'GET','path':'/orders/{order_id}'},"
        "{'id':'response-property-type-changed','text':'response property type changed','level':3,"
        "'operation':'GET','path':'/orders/{order_id}'}]))\n"
        "sys.exit(1)\n",
    )
    consumer = json.loads((FIXTURES / "consumer-used-operation.json").read_text())
    provider = json.loads((FIXTURES / "provider-breaking-used-schema.json").read_text())

    old_value = os.environ.get("INVOCATION_LOG")
    os.environ["INVOCATION_LOG"] = str(invocation_log)
    try:
        findings = run_oasdiff(
            consumer,
            provider,
            frozenset({("get", "/orders/{order_id}")}),
            binary,
            timeout_seconds=1,
        )
    finally:
        if old_value is None:
            os.environ.pop("INVOCATION_LOG", None)
        else:
            os.environ["INVOCATION_LOG"] = old_value

    assert [(item.severity, item.code, item.location, item.message) for item in findings] == [
        (
            "ERR",
            "response-property-type-changed",
            "GET /orders/{order_id}",
            "response property type changed",
        ),
        (
            "WARN",
            "response-optional-property-updated",
            "GET /orders/{order_id}",
            "optional response property changed",
        ),
    ]
    invocations = [json.loads(line) for line in invocation_log.read_text().splitlines()]
    assert len(invocations) == 1
    assert invocations[0][0] == "breaking"
    assert "--allow-external-refs=false" in invocations[0]
    assert invocations[0][-1] == "--fail-on=WARN"


def test_run_oasdiff_timeout_is_bounded_and_redacted(tmp_path: Path) -> None:
    binary = tmp_path / "oasdiff"
    _write_executable(binary, "import time\ntime.sleep(2)\n")

    with pytest.raises(CheckerTimeoutError, match="oasdiff exceeded configured timeout"):
        run_oasdiff(
            {"openapi": "3.0.3", "paths": {}},
            {"openapi": "3.0.3", "paths": {}},
            frozenset(),
            binary,
            timeout_seconds=0.01,
        )


def test_run_oasdiff_exception_does_not_expose_stderr(tmp_path: Path) -> None:
    binary = tmp_path / "oasdiff"
    _write_executable(binary, "import sys\nsys.stderr.write('SECRET_RESPONSE_BODY')\nsys.exit(2)\n")

    with pytest.raises(CheckerExecutionError, match="oasdiff execution failed") as error:
        run_oasdiff(
            {"openapi": "3.0.3", "paths": {}},
            {"openapi": "3.0.3", "paths": {}},
            frozenset(),
            binary,
            timeout_seconds=1,
        )

    assert "SECRET_RESPONSE_BODY" not in str(error.value)


def _candidate_fingerprints() -> tuple[dict[str, str], dict[str, str]]:
    frontend = {
        "consumer_openapi_sha256": "1" * 64,
        "required_operations_sha256": "2" * 64,
        "required_permissions_sha256": "3" * 64,
        "dependencies_sha256": "4" * 64,
        "recipe_sha256": "5" * 64,
    }
    backend = {
        "provider_openapi_sha256": "6" * 64,
        "provided_permissions_sha256": "7" * 64,
        "migration_tree_sha256": "8" * 64,
        "dependencies_sha256": "9" * 64,
        "recipe_sha256": "a" * 64,
        "expected_schema_head": "head-new",
    }
    return frontend, backend


def _input_set(value: str) -> dict[str, Any]:
    return {
        "kind": "wes.release.input-set.v1",
        "files": [{"path": "deploy/runtime.conf", "sha256": value * 64}],
    }


def _current_evidence() -> dict[str, Any]:
    frontend, backend = _candidate_fingerprints()
    return {
        "kind": "wes.release.current-fingerprints.v1",
        "frontend": frontend,
        "backend": backend,
        "deploy": _input_set("b"),
        "runtime": _input_set("c"),
    }


def _effective_facts(relation: str = "equal") -> dict[str, Any]:
    return {
        "kind": "wes.release.effective-facts.v1",
        "deploy": _input_set("b"),
        "runtime": _input_set("c"),
        "database": {"current_heads": ["head-new"], "relation_to_candidate": relation},
    }


def test_classification_is_fast_for_equal_content_even_with_different_revisions() -> None:
    frontend, backend = _candidate_fingerprints()

    decision = classify_release_mode(frontend, backend, _current_evidence(), _effective_facts())

    assert decision.auto_mode == "FAST"
    assert decision.effective_mode == "FAST"
    assert decision.reasons == ()


@pytest.mark.parametrize(
    ("side", "key", "reason"),
    [
        ("frontend", "consumer_openapi_sha256", "frontend.consumer-openapi.changed"),
        ("frontend", "required_operations_sha256", "frontend.required-operations.changed"),
        ("frontend", "required_permissions_sha256", "frontend.required-permissions.changed"),
        ("frontend", "dependencies_sha256", "frontend.dependencies.changed"),
        ("frontend", "recipe_sha256", "frontend.recipe.changed"),
        ("backend", "provider_openapi_sha256", "backend.provider-openapi.changed"),
        ("backend", "provided_permissions_sha256", "backend.provided-permissions.changed"),
        ("backend", "migration_tree_sha256", "backend.migration-tree.changed"),
        ("backend", "dependencies_sha256", "backend.dependencies.changed"),
        ("backend", "recipe_sha256", "backend.recipe.changed"),
        ("backend", "expected_schema_head", "backend.expected-schema-head.changed"),
    ],
)
def test_each_image_fingerprint_change_selects_full(side: str, key: str, reason: str) -> None:
    frontend, backend = _candidate_fingerprints()
    current = _current_evidence()
    current[side][key] = "f" * 64

    decision = classify_release_mode(frontend, backend, current, _effective_facts())

    assert decision.auto_mode == "FULL"
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("group", "reason"),
    [("deploy", "deploy.inputs.changed"), ("runtime", "runtime.inputs.changed")],
)
def test_effective_deploy_or_runtime_change_selects_full(group: str, reason: str) -> None:
    frontend, backend = _candidate_fingerprints()
    effective = _effective_facts()
    effective[group] = _input_set("d")

    decision = classify_release_mode(frontend, backend, _current_evidence(), effective)

    assert decision.auto_mode == "FULL"
    assert decision.reasons == (reason,)


def test_missing_current_evidence_is_full() -> None:
    frontend, backend = _candidate_fingerprints()

    decision = classify_release_mode(frontend, backend, None, _effective_facts())

    assert decision.auto_mode == "FULL"
    assert decision.reasons == ("current-evidence.missing",)


def test_force_full_can_only_upgrade_fast() -> None:
    frontend, backend = _candidate_fingerprints()

    decision = classify_release_mode(frontend, backend, _current_evidence(), _effective_facts(), force_full=True)

    assert decision.auto_mode == "FAST"
    assert decision.effective_mode == "FULL"
    assert decision.reasons == ("operator.force-full",)


@pytest.mark.parametrize("relation", ["unknown", "diverged", "downgrade"])
def test_unknown_diverged_or_downgrade_database_relation_aborts(relation: str) -> None:
    frontend, backend = _candidate_fingerprints()

    with pytest.raises(ClassificationError, match="database relation"):
        classify_release_mode(frontend, backend, _current_evidence(), _effective_facts(relation))


def test_multiple_database_heads_abort() -> None:
    frontend, backend = _candidate_fingerprints()
    effective = _effective_facts()
    effective["database"]["current_heads"] = ["one", "two"]

    with pytest.raises(ClassificationError, match="exactly one current head"):
        classify_release_mode(frontend, backend, _current_evidence(), effective)


def test_known_ancestor_selects_full_forward_migration() -> None:
    frontend, backend = _candidate_fingerprints()
    effective = _effective_facts("ancestor")
    effective["database"]["current_heads"] = ["head-old"]

    decision = classify_release_mode(frontend, backend, _current_evidence(), effective)

    assert decision.auto_mode == "FULL"
    assert decision.reasons == ("database.forward-migration",)


def test_equal_database_relation_requires_exact_candidate_head() -> None:
    frontend, backend = _candidate_fingerprints()
    effective = _effective_facts()
    effective["database"]["current_heads"] = ["wrong-head"]

    with pytest.raises(ClassificationError, match="equal database relation"):
        classify_release_mode(frontend, backend, _current_evidence(), effective)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda current, _effective: current.update({"unknown": True}),
        lambda _current, effective: effective["deploy"]["files"].append({"path": "../secret", "sha256": "d" * 64}),
        lambda _current, effective: effective["runtime"]["files"].extend(
            [
                {"path": "a", "sha256": "d" * 64},
                {"path": "a", "sha256": "e" * 64},
            ]
        ),
        lambda _current, effective: effective["deploy"]["files"][0].update(
            {"path": "./docker-compose.test-deploy.yml"}
        ),
    ],
)
def test_classification_rejects_unknown_or_noncanonical_evidence(mutation: Any) -> None:
    frontend, backend = _candidate_fingerprints()
    current = _current_evidence()
    effective = _effective_facts()
    mutation(current, effective)

    with pytest.raises(ArtifactValidationError):
        classify_release_mode(frontend, backend, current, effective)


def test_report_is_deterministic_and_warn_approval_is_digest_bound() -> None:
    frontend_digest = f"sha256:{'1' * 64}"
    backend_digest = f"sha256:{'2' * 64}"
    checker_digest = f"sha256:{'3' * 64}"
    warning = (
        # Intentionally supplied out of order; report order is contractual.
        Finding("WARN", "oasdiff-warning", "GET /orders", "optional response changed"),
    )
    frontend, backend = _candidate_fingerprints()
    decision = classify_release_mode(frontend, backend, _current_evidence(), _effective_facts())

    report = build_compatibility_report(
        release_id="release-42",
        deploy_scope="FRONTEND",
        candidate_digests={"frontend": frontend_digest},
        current_digests={"frontend": frontend_digest, "backend": backend_digest},
        checker_digest=checker_digest,
        artifact_hashes={
            "frontend": {
                "consumer_openapi": "1" * 64,
                "required_operations": "2" * 64,
                "required_permissions": "3" * 64,
            },
            "backend": {"provider_openapi": "4" * 64, "provided_permissions": "5" * 64},
        },
        mode=decision,
        findings=warning,
        approval_reason="已评估可选响应字段变更",
    )

    assert list(report) == [
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
    ]
    assert report["compatibility"]["status"] == "WARN"
    assert report["pre_cutover_state"] == "READY"
    assert report["approval"] == {
        "frontend_digest": frontend_digest,
        "backend_digest": backend_digest,
        "checker_digest": checker_digest,
        "diff_hash": report["compatibility"]["diff_hash"],
        "reason": "已评估可选响应字段变更",
    }
    validate_compatibility_report(report)


def test_warn_without_approval_and_err_with_approval_are_pre_cutover_aborted() -> None:
    frontend, backend = _candidate_fingerprints()
    decision = classify_release_mode(frontend, backend, _current_evidence(), _effective_facts())
    common = {
        "release_id": "release-42",
        "deploy_scope": "BOTH",
        "candidate_digests": {"frontend": f"sha256:{'1' * 64}", "backend": f"sha256:{'2' * 64}"},
        "current_digests": {"frontend": f"sha256:{'1' * 64}", "backend": f"sha256:{'2' * 64}"},
        "checker_digest": f"sha256:{'3' * 64}",
        "artifact_hashes": {
            "frontend": {
                "consumer_openapi": "1" * 64,
                "required_operations": "2" * 64,
                "required_permissions": "3" * 64,
            },
            "backend": {"provider_openapi": "4" * 64, "provided_permissions": "5" * 64},
        },
        "mode": decision,
    }
    warn = Finding("WARN", "warning", "openapi", "warning")
    err = check_required_permissions(("missing",), frozenset())[0]

    warn_report = build_compatibility_report(**common, findings=(warn,))
    err_report = build_compatibility_report(**common, findings=(err,), approval_reason="cannot approve ERR")

    assert warn_report["approval"] is None
    assert warn_report["pre_cutover_state"] == "PRE_CUTOVER_ABORTED"
    assert err_report["approval"] is None
    assert err_report["pre_cutover_state"] == "PRE_CUTOVER_ABORTED"


def test_report_validator_rejects_unknown_fields_and_wrong_digest_scope() -> None:
    frontend, backend = _candidate_fingerprints()
    decision = classify_release_mode(frontend, backend, _current_evidence(), _effective_facts())
    report = build_compatibility_report(
        release_id="release-42",
        deploy_scope="BACKEND",
        candidate_digests={"backend": f"sha256:{'2' * 64}"},
        current_digests={"frontend": f"sha256:{'1' * 64}", "backend": f"sha256:{'2' * 64}"},
        checker_digest=f"sha256:{'3' * 64}",
        artifact_hashes={
            "frontend": {
                "consumer_openapi": "1" * 64,
                "required_operations": "2" * 64,
                "required_permissions": "3" * 64,
            },
            "backend": {"provider_openapi": "4" * 64, "provided_permissions": "5" * 64},
        },
        mode=decision,
        findings=(),
    )
    report["unknown"] = True

    with pytest.raises(ArtifactValidationError, match="report fields"):
        validate_compatibility_report(report)

    report.pop("unknown")
    report["candidate_digests"] = {"frontend": f"sha256:{'1' * 64}"}
    with pytest.raises(ArtifactValidationError, match="candidate digest scope"):
        validate_compatibility_report(report)


def _valid_pass_report() -> dict[str, Any]:
    frontend, backend = _candidate_fingerprints()
    decision = classify_release_mode(frontend, backend, _current_evidence(), _effective_facts())
    return build_compatibility_report(
        release_id="release-42",
        deploy_scope="BOTH",
        candidate_digests={"frontend": f"sha256:{'1' * 64}", "backend": f"sha256:{'2' * 64}"},
        current_digests={"frontend": f"sha256:{'1' * 64}", "backend": f"sha256:{'2' * 64}"},
        checker_digest=f"sha256:{'3' * 64}",
        artifact_hashes={
            "frontend": {
                "consumer_openapi": "1" * 64,
                "required_operations": "2" * 64,
                "required_permissions": "3" * 64,
            },
            "backend": {"provider_openapi": "4" * 64, "provided_permissions": "5" * 64},
        },
        mode=decision,
        findings=(),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report.update({"kind": "wrong"}),
        lambda report: report.update({"release_id": ""}),
        lambda report: report.update({"deploy_scope": "ALL"}),
        lambda report: report["current_digests"].pop("backend"),
        lambda report: report.update({"checker_digest": "sha256:" + "A" * 64}),
        lambda report: report["artifact_hashes"]["frontend"].update({"unknown": "1" * 64}),
        lambda report: report.update({"auto_mode": "AUTO"}),
        lambda report: report.update({"auto_mode": "FULL", "effective_mode": "FAST"}),
        lambda report: report.update({"mode_reasons": ["duplicate", "duplicate"]}),
        lambda report: report["compatibility"].update({"unknown": True}),
        lambda report: report["compatibility"].update({"status": "WARN"}),
        lambda report: report["compatibility"].update({"diff_hash": "f" * 64}),
        lambda report: report.update(
            {
                "approval": {
                    "frontend_digest": report["candidate_digests"]["frontend"],
                    "backend_digest": report["candidate_digests"]["backend"],
                    "checker_digest": report["checker_digest"],
                    "diff_hash": report["compatibility"]["diff_hash"],
                    "reason": "PASS 不应有 approval",
                }
            }
        ),
        lambda report: report.update({"pre_cutover_state": "FAILED"}),
        lambda report: report.update({"pre_cutover_state": "PRE_CUTOVER_ABORTED"}),
    ],
)
def test_report_validator_rejects_each_noncanonical_shape_or_enum(mutation: Any) -> None:
    report = deepcopy(_valid_pass_report())
    mutation(report)

    with pytest.raises(ArtifactValidationError):
        validate_compatibility_report(report)


def _write_cli_json(tmp_path: Path, name: str, value: Any) -> Path:
    path = tmp_path / name
    path.write_bytes(_json_bytes(value))
    return path


def _cli_args(tmp_path: Path) -> tuple[list[str], Path]:
    frontend, backend, frontend_labels, backend_labels = _valid_release_inputs(tmp_path)
    artifacts = load_release_artifacts(frontend, backend, frontend_labels, backend_labels)
    current = {
        "kind": "wes.release.current-fingerprints.v1",
        "frontend": artifacts.frontend_fingerprints,
        "backend": artifacts.backend_fingerprints,
        "deploy": _input_set("b"),
        "runtime": _input_set("c"),
    }
    effective = {
        "kind": "wes.release.effective-facts.v1",
        "deploy": _input_set("b"),
        "runtime": _input_set("c"),
        "database": {
            "current_heads": [artifacts.backend_fingerprints["expected_schema_head"]],
            "relation_to_candidate": "equal",
        },
    }
    output = tmp_path / "compatibility-report.json"
    args = [
        "--frontend-dir",
        str(frontend),
        "--backend-dir",
        str(backend),
        "--frontend-labels",
        str(_write_cli_json(tmp_path, "frontend-labels.json", frontend_labels)),
        "--backend-labels",
        str(_write_cli_json(tmp_path, "backend-labels.json", backend_labels)),
        "--current-fingerprints",
        str(_write_cli_json(tmp_path, "current-fingerprints.json", current)),
        "--effective-facts",
        str(_write_cli_json(tmp_path, "effective-facts.json", effective)),
        "--release-id",
        "release-42",
        "--deploy-scope",
        "BOTH",
        "--candidate-digests",
        str(
            _write_cli_json(
                tmp_path,
                "candidate-digests.json",
                {"frontend": f"sha256:{'1' * 64}", "backend": f"sha256:{'2' * 64}"},
            )
        ),
        "--current-digests",
        str(
            _write_cli_json(
                tmp_path,
                "current-digests.json",
                {"frontend": f"sha256:{'1' * 64}", "backend": f"sha256:{'2' * 64}"},
            )
        ),
        "--checker-digest",
        f"sha256:{'3' * 64}",
        "--output",
        str(output),
    ]
    return args, output


def test_cli_writes_one_deterministic_pass_report(tmp_path: Path) -> None:
    args, output = _cli_args(tmp_path)
    binary = tmp_path / "oasdiff"
    _write_executable(binary, "print('[]')\n")

    first_exit = main(args, oasdiff_bin=binary)
    first_bytes = output.read_bytes()
    second_exit = main(args, oasdiff_bin=binary)

    assert first_exit == second_exit == 0
    assert output.read_bytes() == first_bytes
    report = json.loads(first_bytes)
    assert report["compatibility"]["status"] == "PASS"
    assert report["pre_cutover_state"] == "READY"


@pytest.mark.parametrize(
    "evidence_case",
    ["omitted", "missing-file", "unreadable", "bad-json", "invalid-structure"],
)
def test_cli_treats_unavailable_current_evidence_as_full(tmp_path: Path, evidence_case: str) -> None:
    args, output = _cli_args(tmp_path)
    current_flag = args.index("--current-fingerprints")
    current_path = Path(args[current_flag + 1])
    if evidence_case == "omitted":
        del args[current_flag : current_flag + 2]
    elif evidence_case == "missing-file":
        current_path.unlink()
    elif evidence_case == "unreadable":
        current_path.unlink()
        current_path.mkdir()
    elif evidence_case == "bad-json":
        current_path.write_text("{not-json}\n", encoding="utf-8")
    else:
        current_path.write_bytes(_json_bytes({"kind": "wes.release.current-fingerprints.v1"}))
    binary = tmp_path / "oasdiff"
    _write_executable(binary, "print('[]')\n")

    exit_code = main(args, oasdiff_bin=binary)

    assert exit_code == 0
    report = json.loads(output.read_text())
    assert report["auto_mode"] == report["effective_mode"] == "FULL"
    assert report["mode_reasons"] == ["current-evidence.missing"]
    assert report["compatibility"]["status"] == "PASS"
    assert report["pre_cutover_state"] == "READY"


def test_cli_timeout_writes_redacted_pre_cutover_abort_report(tmp_path: Path) -> None:
    args, output = _cli_args(tmp_path)
    binary = tmp_path / "oasdiff"
    _write_executable(binary, "import sys, time\nsys.stderr.write('SECRET_CONFIG_CONTENT')\ntime.sleep(2)\n")

    exit_code = main(args, oasdiff_bin=binary, timeout_seconds=0.01)

    assert exit_code == 1
    report_bytes = output.read_bytes()
    assert b"SECRET_CONFIG_CONTENT" not in report_bytes
    report = json.loads(report_bytes)
    assert report["compatibility"]["status"] == "ERR"
    assert report["compatibility"]["findings"] == [
        {
            "code": "checker-oasdiff-timeout",
            "severity": "ERR",
            "location": "checker",
            "message": "OpenAPI compatibility check exceeded its hard timeout",
        }
    ]
    assert report["pre_cutover_state"] == "PRE_CUTOVER_ABORTED"


def test_cli_database_divergence_writes_pre_cutover_abort_report(tmp_path: Path) -> None:
    args, output = _cli_args(tmp_path)
    effective_path = Path(args[args.index("--effective-facts") + 1])
    effective = json.loads(effective_path.read_text())
    effective["database"]["relation_to_candidate"] = "diverged"
    effective_path.write_bytes(_json_bytes(effective))
    binary = tmp_path / "oasdiff"
    _write_executable(binary, "print('[]')\n")

    exit_code = main(args, oasdiff_bin=binary)

    assert exit_code == 1
    report = json.loads(output.read_text())
    assert report["auto_mode"] == "FULL"
    assert report["pre_cutover_state"] == "PRE_CUTOVER_ABORTED"
    assert report["compatibility"]["findings"] == [
        {
            "code": "database-state-not-deployable",
            "severity": "ERR",
            "location": "database",
            "message": "Database state is not an equal or known-ancestor release state",
        }
    ]


def test_cli_has_force_full_but_structurally_rejects_force_fast(tmp_path: Path) -> None:
    args, output = _cli_args(tmp_path)
    binary = tmp_path / "oasdiff"
    _write_executable(binary, "print('[]')\n")

    assert main([*args, "--force-full"], oasdiff_bin=binary) == 0
    assert json.loads(output.read_text())["effective_mode"] == "FULL"
    with pytest.raises(SystemExit) as error:
        main([*args, "--force-fast"], oasdiff_bin=binary)
    assert error.value.code == 2
