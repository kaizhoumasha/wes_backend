from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tools.release_checker.release_checker import canonical_json_bytes, project_selected_operations

FIXTURES = Path(__file__).parent / "fixtures"
GET_ORDER = frozenset({("get", "/orders/{order_id}")})
HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})
OASDIFF_VERSION = "oasdiff version 1.28.0"
OASDIFF_DARWIN_ALL_BINARY_SHA256 = "1e3c6aacd8ae95f04beb904363be13b73b2c8c732f39d5a3db8b3e40aa936cc3"
OASDIFF_LINUX_BINARY_SHA256 = {
    "aarch64": "2ac47b3efc8ba716afd52839596513ff0e7e0d273ec58842a41b779f2435decd",
    "x86_64": "69f23d7d1899ba1bd27af6c1dd3db18c425937f7b85c0a3ad72efd01670957d2",
}


@dataclass(frozen=True)
class OasdiffResult:
    returncode: int
    stdout: str


class OasdiffExecutionError(RuntimeError):
    pass


def load_spec(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def oasdiff_bin() -> str:
    binary = os.environ.get("OASDIFF_BIN")
    assert binary, "set OASDIFF_BIN to the pinned oasdiff 1.28.0 binary"
    return binary


def compare_consumer_to_provider(
    consumer_baseline: dict[str, Any],
    selected_provider: dict[str, Any],
    selected_operations: frozenset[tuple[str, str]],
    work_dir: Path,
    *,
    oasdiff_bin: str,
) -> OasdiffResult:
    base_path = work_dir / "consumer-baseline.json"
    revision_path = work_dir / "selected-provider.json"
    base_path.write_bytes(canonical_json_bytes(project_selected_operations(consumer_baseline, selected_operations)))
    revision_path.write_bytes(canonical_json_bytes(project_selected_operations(selected_provider, selected_operations)))
    completed = subprocess.run(  # noqa: S603 - binary path is digest-pinned by this test
        [
            oasdiff_bin,
            "breaking",
            str(base_path),
            str(revision_path),
            "--allow-external-refs=false",
            "--format=json",
            "--fail-on=WARN",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.stderr:
        raise OasdiffExecutionError(completed.stderr.strip())
    return OasdiffResult(completed.returncode, completed.stdout)


def test_projection_keeps_only_selected_method_and_all_components() -> None:
    source = load_spec("consumer-used-operation.json")

    projected = project_selected_operations(source, GET_ORDER)

    assert set(projected["paths"]) == {"/orders/{order_id}"}
    assert set(projected["paths"]["/orders/{order_id}"]) == {"get"}
    assert projected["components"] == source["components"]


def test_selected_path_item_external_ref_is_rejected_before_projection() -> None:
    source = load_spec("consumer-used-operation.json")
    source["paths"]["/orders/{order_id}"] = {"$ref": "https://example.invalid/path-item.json"}

    with pytest.raises(
        ValueError,
        match=r"selected Path Item.*contains unsupported.*external",
    ):
        project_selected_operations(source, GET_ORDER)


def test_pinned_oasdiff_binary_is_available(oasdiff_bin: str) -> None:
    completed = subprocess.run(  # noqa: S603 - explicit OASDIFF_BIN is the test subject
        [oasdiff_bin, "--version"], check=True, capture_output=True, text=True
    )
    assert completed.stdout.strip() == OASDIFF_VERSION
    expected_sha256 = (
        OASDIFF_DARWIN_ALL_BINARY_SHA256
        if platform.system() == "Darwin"
        else OASDIFF_LINUX_BINARY_SHA256[platform.machine()]
    )
    assert hashlib.sha256(Path(oasdiff_bin).read_bytes()).hexdigest() == expected_sha256


def test_unselected_endpoint_and_transitive_schema_change_is_ignored(tmp_path: Path, oasdiff_bin: str) -> None:
    result = compare_consumer_to_provider(
        load_spec("consumer-used-operation.json"),
        load_spec("provider-compatible-unused-change.json"),
        GET_ORDER,
        tmp_path,
        oasdiff_bin=oasdiff_bin,
    )

    assert result == OasdiffResult(returncode=0, stdout="[]\n")


def test_breaking_unselected_method_on_selected_path_is_ignored(tmp_path: Path, oasdiff_bin: str) -> None:
    result = compare_consumer_to_provider(
        load_spec("consumer-used-operation.json"),
        load_spec("provider-breaking-unused-method.json"),
        GET_ORDER,
        tmp_path,
        oasdiff_bin=oasdiff_bin,
    )

    assert result == OasdiffResult(returncode=0, stdout="[]\n")


def test_deleting_selected_operation_is_error_and_order_is_consumer_to_provider(
    tmp_path: Path, oasdiff_bin: str
) -> None:
    result = compare_consumer_to_provider(
        load_spec("consumer-used-operation.json"),
        load_spec("provider-breaking-unused-method.json"),
        frozenset({("post", "/orders/{order_id}")}),
        tmp_path,
        oasdiff_bin=oasdiff_bin,
    )

    assert result.returncode == 1
    assert "api-path-removed-without-deprecation" in result.stdout
    assert "/orders/{order_id}" in result.stdout


def test_breaking_transitive_schema_of_selected_operation_is_error(tmp_path: Path, oasdiff_bin: str) -> None:
    result = compare_consumer_to_provider(
        load_spec("consumer-used-operation.json"),
        load_spec("provider-breaking-used-schema.json"),
        GET_ORDER,
        tmp_path,
        oasdiff_bin=oasdiff_bin,
    )

    assert result.returncode == 1
    assert "response-property-type-changed" in result.stdout
    assert "data/status" in result.stdout


def test_new_consumer_without_menu_accepts_old_provider_extra_response_field(tmp_path: Path, oasdiff_bin: str) -> None:
    result = compare_consumer_to_provider(
        load_spec("menu-old-consumer-new-provider.json"),
        load_spec("menu-new-consumer-old-provider.json"),
        frozenset({("get", "/health")}),
        tmp_path,
        oasdiff_bin=oasdiff_bin,
    )

    assert result == OasdiffResult(returncode=0, stdout="[]\n")


def test_old_consumer_requiring_menu_rejects_new_provider(tmp_path: Path, oasdiff_bin: str) -> None:
    result = compare_consumer_to_provider(
        load_spec("menu-new-consumer-old-provider.json"),
        load_spec("menu-old-consumer-new-provider.json"),
        frozenset({("get", "/auth/my.menus")}),
        tmp_path,
        oasdiff_bin=oasdiff_bin,
    )

    assert result.returncode == 1
    assert "/auth/my.menus" in result.stdout


def test_comparison_invokes_oasdiff_once_with_base_then_revision(
    tmp_path: Path, oasdiff_bin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocation_log = tmp_path / "invocations.jsonl"
    wrapper = tmp_path / "counting-oasdiff"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['OASDIFF_INVOCATION_LOG'], 'a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "os.execv(os.environ['REAL_OASDIFF_BIN'], "
        "[os.environ['REAL_OASDIFF_BIN'], *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("OASDIFF_INVOCATION_LOG", str(invocation_log))
    monkeypatch.setenv("REAL_OASDIFF_BIN", oasdiff_bin)
    compare_consumer_to_provider(
        load_spec("consumer-used-operation.json"),
        load_spec("provider-compatible-unused-change.json"),
        GET_ORDER,
        tmp_path,
        oasdiff_bin=str(wrapper),
    )

    invocations = [json.loads(line) for line in invocation_log.read_text().splitlines()]
    assert len(invocations) == 1
    assert invocations[0][0:3] == [
        "breaking",
        str(tmp_path / "consumer-baseline.json"),
        str(tmp_path / "selected-provider.json"),
    ]
    assert "--allow-external-refs=false" in invocations[0]


def test_external_reference_is_rejected_by_oasdiff_without_custom_ref_walker(tmp_path: Path, oasdiff_bin: str) -> None:
    provider = load_spec("provider-compatible-unused-change.json")
    provider["paths"]["/orders/{order_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
        "$ref": "https://example.invalid/order.json"
    }

    with pytest.raises(OasdiffExecutionError, match="external"):
        compare_consumer_to_provider(
            load_spec("consumer-used-operation.json"),
            provider,
            GET_ORDER,
            tmp_path,
            oasdiff_bin=oasdiff_bin,
        )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "consumer-used-operation.json",
        "provider-compatible-unused-change.json",
        "provider-breaking-unused-method.json",
        "provider-breaking-used-schema.json",
        "menu-new-consumer-old-provider.json",
        "menu-old-consumer-new-provider.json",
    ],
)
def test_fixture_is_openapi_document(fixture_name: str) -> None:
    document = load_spec(fixture_name)
    assert document["openapi"] == "3.0.3"
    assert isinstance(document["paths"], dict)
