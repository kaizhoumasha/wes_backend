from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI

from scripts import export_release_provider as exporter
from src.utils import permission_scanner as permission_scanner_module

DEPENDENCY_PATHS = (
    "packages/wes_plugin_sdk/pyproject.toml",
    "pyproject.toml",
    "uv.lock",
    "workline_plugins/rough_sorter/pyproject.toml",
)
MIGRATION_PATHS = (
    "alembic.ini",
    "migrations/env.py",
    "migrations/versions/0001_initial.py",
    "migrations/versions/0002_head.py",
)
RECIPE_PATHS = ("Dockerfile", "main.py")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(root: Path) -> None:
    for relative_path in DEPENDENCY_PATHS:
        _write(root / relative_path, f"dependency={relative_path}\n")
    _write(root / "Dockerfile", 'CMD ["uvicorn", "main:app"]\n')
    _write(root / "main.py", "from src.register import create_app\napp = create_app()\n")
    _write(root / "alembic.ini", "[alembic]\nscript_location = %(here)s/migrations\n")
    _write(root / "migrations/env.py", "# production migration environment\n")
    _write(
        root / "migrations/versions/0001_initial.py",
        'revision = "111111111111"\ndown_revision = None\n',
    )
    _write(
        root / "migrations/versions/0002_head.py",
        'revision = "222222222222"\ndown_revision = "111111111111"\n',
    )
    _write(root / "migrations/README", "human documentation excluded\n")
    _write(root / "migrations/script.py.mako", "future migration template excluded\n")


def _permission_dependency(permission_name: str) -> Any:
    async def dependency() -> None:
        return None

    dependency.permission_required = permission_name
    dependency.is_rbac = True
    return dependency


def _make_app(*, title: str = "仓储执行系统", permission_name: str = "admin:user:list") -> FastAPI:
    app = FastAPI(title=title)

    async def list_users() -> None:
        return None

    app.add_api_route(
        "/users",
        list_users,
        methods=["GET"],
        dependencies=[Depends(_permission_dependency(permission_name))],
        summary="用户列表",
    )
    return app


def _read_artifacts(out_dir: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(out_dir.iterdir()) if path.is_file()}


def _input_set_digest(root: Path, paths: tuple[str, ...]) -> str:
    payload = {
        "kind": "wes.release.input-set.v1",
        "files": [
            {"path": path, "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()} for path in sorted(paths)
        ],
    }
    canonical_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


@pytest.fixture
def provider_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "provider-repo"
    _make_repo(repo_root)
    return repo_root


def test_direct_script_cli_exports_from_repository_root(tmp_path: Path) -> None:
    out_dir = tmp_path / "direct-cli-provider"

    result = subprocess.run(
        [sys.executable, "scripts/export_release_provider.py", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in out_dir.iterdir()} == {
        "provided-permissions.json",
        "provider-fingerprints.json",
        "provider-openapi.json",
    }


def test_export_is_byte_identical_utf8_and_matches_exact_schemas(
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app()
    monkeypatch.setattr(exporter, "create_app", lambda: app)
    out_dir = tmp_path / "provider"

    exporter.export_release_provider(out_dir, repo_root=provider_repo)
    first = _read_artifacts(out_dir)
    exporter.export_release_provider(out_dir, repo_root=provider_repo)
    second = _read_artifacts(out_dir)

    assert first == second
    assert set(first) == {
        "provided-permissions.json",
        "provider-fingerprints.json",
        "provider-openapi.json",
    }
    assert "仓储执行系统".encode() in first["provider-openapi.json"]
    assert b"\\u4ed3" not in first["provider-openapi.json"]
    permissions = json.loads(first["provided-permissions.json"])
    assert permissions == {
        "kind": "wes.release.provided-permissions.v1",
        "permissions": [
            {
                "action": "list",
                "category": "admin",
                "description": "用户列表",
                "method": "GET",
                "name": "admin:user:list",
                "path": "/users",
                "resource": "user",
                "type": "user_api",
            }
        ],
    }
    fingerprints = json.loads(first["provider-fingerprints.json"])
    assert set(fingerprints) == {
        "dependencies_sha256",
        "expected_schema_head",
        "kind",
        "migration_tree_sha256",
        "provided_permissions_sha256",
        "provider_openapi_sha256",
        "recipe_sha256",
    }
    assert fingerprints["kind"] == "wes.release.backend-fingerprints.v1"
    assert fingerprints["expected_schema_head"] == "222222222222"
    for field in set(fingerprints) - {"kind", "expected_schema_head"}:
        assert re.fullmatch(r"[0-9a-f]{64}", fingerprints[field])
    assert fingerprints["provider_openapi_sha256"] == hashlib.sha256(first["provider-openapi.json"]).hexdigest()
    assert fingerprints["provided_permissions_sha256"] == hashlib.sha256(first["provided-permissions.json"]).hexdigest()
    assert fingerprints["dependencies_sha256"] == _input_set_digest(provider_repo, DEPENDENCY_PATHS)
    assert fingerprints["migration_tree_sha256"] == _input_set_digest(provider_repo, MIGRATION_PATHS)
    assert fingerprints["recipe_sha256"] == _input_set_digest(provider_repo, RECIPE_PATHS)


def test_openapi_and_permission_facts_change_their_own_raw_byte_fingerprints(
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app()
    monkeypatch.setattr(exporter, "create_app", lambda: app)
    first_dir = tmp_path / "first"
    exporter.export_release_provider(first_dir, repo_root=provider_repo)
    first = json.loads((first_dir / "provider-fingerprints.json").read_bytes())

    monkeypatch.setattr(exporter, "create_app", lambda: _make_app(title="另一个 OpenAPI 标题"))
    second_dir = tmp_path / "second"
    exporter.export_release_provider(second_dir, repo_root=provider_repo)
    second = json.loads((second_dir / "provider-fingerprints.json").read_bytes())
    assert second["provider_openapi_sha256"] != first["provider_openapi_sha256"]
    assert second["provided_permissions_sha256"] == first["provided_permissions_sha256"]

    monkeypatch.setattr(
        exporter,
        "build_validated_permission_leaves",
        lambda _app: [
            {
                "name": "admin:user:create",
                "type": "user_api",
                "category": "admin",
                "description": "创建用户",
                "resource": "user",
                "action": "create",
                "method": "POST",
                "path": "/users",
            }
        ],
    )
    third_dir = tmp_path / "third"
    exporter.export_release_provider(third_dir, repo_root=provider_repo)
    third = json.loads((third_dir / "provider-fingerprints.json").read_bytes())
    assert third["provided_permissions_sha256"] != second["provided_permissions_sha256"]


def test_provided_permissions_use_validated_helper_order(
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exporter, "create_app", _make_app)
    create_leaf = {
        "name": "admin:user:create",
        "type": "user_api",
        "category": "admin",
        "description": "创建用户",
        "resource": "user",
        "action": "create",
        "method": "POST",
        "path": "/users",
    }
    list_leaf = {
        "name": "admin:user:list",
        "type": "user_api",
        "category": "admin",
        "description": "用户列表",
        "resource": "user",
        "action": "list",
        "method": "GET",
        "path": "/users",
    }
    monkeypatch.setattr(
        permission_scanner_module,
        "scan_routes_for_permissions",
        lambda _app: [list_leaf, create_leaf],
    )
    out_dir = tmp_path / "provider"

    exporter.export_release_provider(out_dir, repo_root=provider_repo)

    permissions = json.loads((out_dir / "provided-permissions.json").read_bytes())["permissions"]
    assert [permission["name"] for permission in permissions] == ["admin:user:create", "admin:user:list"]


@pytest.mark.parametrize("relative_path", DEPENDENCY_PATHS)
def test_dependency_fingerprint_covers_only_dockerfile_consumed_dependency_inputs(
    relative_path: str,
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exporter, "create_app", _make_app)
    before_dir = tmp_path / "before"
    exporter.export_release_provider(before_dir, repo_root=provider_repo)
    before = json.loads((before_dir / "provider-fingerprints.json").read_bytes())

    _write(provider_repo / relative_path, f"changed={relative_path}\n")
    after_dir = tmp_path / "after"
    exporter.export_release_provider(after_dir, repo_root=provider_repo)
    after = json.loads((after_dir / "provider-fingerprints.json").read_bytes())

    assert after["dependencies_sha256"] != before["dependencies_sha256"]
    assert after["recipe_sha256"] == before["recipe_sha256"]


@pytest.mark.parametrize("relative_path", RECIPE_PATHS)
def test_recipe_fingerprint_covers_production_recipe_and_entrypoint(
    relative_path: str,
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exporter, "create_app", _make_app)
    before_dir = tmp_path / "before"
    exporter.export_release_provider(before_dir, repo_root=provider_repo)
    before = json.loads((before_dir / "provider-fingerprints.json").read_bytes())

    _write(provider_repo / relative_path, f"changed={relative_path}\n")
    after_dir = tmp_path / "after"
    exporter.export_release_provider(after_dir, repo_root=provider_repo)
    after = json.loads((after_dir / "provider-fingerprints.json").read_bytes())

    assert after["recipe_sha256"] != before["recipe_sha256"]
    assert after["dependencies_sha256"] == before["dependencies_sha256"]


def test_migration_fingerprint_and_expected_single_head_follow_production_migrations(
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exporter, "create_app", _make_app)
    before_dir = tmp_path / "before"
    exporter.export_release_provider(before_dir, repo_root=provider_repo)
    before = json.loads((before_dir / "provider-fingerprints.json").read_bytes())

    _write(provider_repo / "migrations/README", "changed human documentation\n")
    _write(provider_repo / "migrations/script.py.mako", "changed generation template\n")
    excluded_dir = tmp_path / "excluded"
    exporter.export_release_provider(excluded_dir, repo_root=provider_repo)
    excluded = json.loads((excluded_dir / "provider-fingerprints.json").read_bytes())
    assert excluded["migration_tree_sha256"] == before["migration_tree_sha256"]

    _write(
        provider_repo / "migrations/versions/0002_head.py",
        'revision = "222222222222"\ndown_revision = "111111111111"\n# changed migration fact\n',
    )
    after_dir = tmp_path / "after"
    exporter.export_release_provider(after_dir, repo_root=provider_repo)
    after = json.loads((after_dir / "provider-fingerprints.json").read_bytes())

    assert before["expected_schema_head"] == after["expected_schema_head"] == "222222222222"
    assert after["migration_tree_sha256"] != before["migration_tree_sha256"]


@pytest.mark.parametrize(
    "invalid_head",
    [None, 123, "", "abc", "A" * 12, "a" * 11, "a" * 13, "a" * 11 + "\n", "$(id)aaaaaa"],
)
def test_image_boundary_rejects_malformed_or_shell_payload_schema_head(
    invalid_head: object,
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "provider"
    artifact_dir.mkdir()
    openapi = b'{"openapi":"3.1.0"}\n'
    permissions = b'{"kind":"wes.release.provided-permissions.v1","permissions":[]}\n'
    (artifact_dir / "provider-openapi.json").write_bytes(openapi)
    (artifact_dir / "provided-permissions.json").write_bytes(permissions)
    expected = {
        "kind": "wes.release.backend-fingerprints.v1",
        "provider_openapi_sha256": hashlib.sha256(openapi).hexdigest(),
        "provided_permissions_sha256": hashlib.sha256(permissions).hexdigest(),
        "migration_tree_sha256": "1" * 64,
        "dependencies_sha256": "2" * 64,
        "recipe_sha256": "3" * 64,
        "expected_schema_head": invalid_head,
    }
    (artifact_dir / "provider-fingerprints.json").write_text(
        json.dumps(expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(exporter.ReleaseProviderExportError, match="expected_schema_head"):
        exporter.validate_release_provider_artifacts(
            artifact_dir,
            expected=expected,  # type: ignore[arg-type]
            revision="a" * 40,
            source_tree="b" * 40,
        )


@pytest.mark.parametrize("invalid_case", ["missing-description", "extra-field"])
def test_export_rejects_invalid_scanned_permission_without_touching_previous_output(
    invalid_case: str,
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exporter, "create_app", _make_app)
    out_dir = tmp_path / "provider"
    out_dir.mkdir()
    _write(out_dir / "sentinel.txt", "old output\n")
    before = _read_artifacts(out_dir)
    invalid_leaf = {
        "name": "admin:user:list",
        "type": "user_api",
        "category": "admin",
        "description": "List users",
        "resource": "user",
        "action": "list",
        "method": "GET",
        "path": "/users",
    }
    if invalid_case == "missing-description":
        invalid_leaf["description"] = None
    else:
        invalid_leaf["unexpected"] = "must fail closed"
    monkeypatch.setattr(permission_scanner_module, "scan_routes_for_permissions", lambda _app: [invalid_leaf])

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="权限叶子字段"):
        exporter.export_release_provider(out_dir, repo_root=provider_repo)

    assert _read_artifacts(out_dir) == before


def test_output_directory_rolls_back_when_promotion_fails(
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exporter, "create_app", _make_app)
    out_dir = tmp_path / "provider"
    out_dir.mkdir()
    _write(out_dir / "sentinel.txt", "old output\n")
    before = _read_artifacts(out_dir)
    real_replace = Path.replace

    def fail_staged_promotion(source: Path, destination: str | Path) -> Path:
        if Path(destination) == out_dir and source.name.startswith(f".{out_dir.name}.tmp-"):
            raise OSError("simulated promotion failure")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_staged_promotion)

    with pytest.raises(OSError, match="simulated promotion failure"):
        exporter.export_release_provider(out_dir, repo_root=provider_repo)

    assert _read_artifacts(out_dir) == before
    assert sorted(path.name for path in out_dir.parent.iterdir()) == sorted([provider_repo.name, out_dir.name])


def test_backup_cleanup_failure_is_non_fatal_after_successful_promotion(
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exporter, "create_app", _make_app)
    out_dir = tmp_path / "provider"
    out_dir.mkdir()
    _write(out_dir / "sentinel.txt", "old output\n")
    real_rmtree = exporter.shutil.rmtree

    def fail_backup_cleanup(path: str | Path, *args: Any, **kwargs: Any) -> None:
        if Path(path).name.startswith(f".{out_dir.name}.backup-"):
            raise OSError("simulated backup cleanup failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(exporter.shutil, "rmtree", fail_backup_cleanup)

    exporter.export_release_provider(out_dir, repo_root=provider_repo)

    assert set(_read_artifacts(out_dir)) == {
        "provided-permissions.json",
        "provider-fingerprints.json",
        "provider-openapi.json",
    }
    assert not (out_dir / "sentinel.txt").exists()
    backup_dirs = [path for path in out_dir.parent.iterdir() if path.name.startswith(f".{out_dir.name}.backup-")]
    assert len(backup_dirs) == 1
    assert (backup_dirs[0] / "sentinel.txt").read_text(encoding="utf-8") == "old output\n"


def test_artifacts_never_contain_secret_values_or_absolute_checkout_paths(
    provider_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "release-export-must-not-leak-this-secret"
    monkeypatch.setenv("RELEASE_EXPORT_TEST_SECRET", secret)
    monkeypatch.setattr(exporter, "create_app", _make_app)
    out_dir = tmp_path / "provider"

    exporter.export_release_provider(out_dir, repo_root=provider_repo)

    artifact_bytes = b"".join(_read_artifacts(out_dir).values())
    assert secret.encode() not in artifact_bytes
    assert str(provider_repo).encode() not in artifact_bytes
