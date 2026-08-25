from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_DEPENDENCY_PATHS = (
    "packages/wes_plugin_sdk/pyproject.toml",
    "pyproject.toml",
    "uv.lock",
    "workline_plugins/rough_sorter/pyproject.toml",
)
_RECIPE_PATHS = ("Dockerfile", "main.py")
_ARTIFACT_NAMES = (
    "provider-openapi.json",
    "provided-permissions.json",
    "provider-fingerprints.json",
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SCHEMA_HEAD_PATTERN = re.compile(r"[0-9a-f]{12}")
_GIT_OBJECT_PATTERN = re.compile(r"[0-9a-f]{40}")


class ReleaseProviderExportError(RuntimeError):
    """后端发布能力合同无法安全导出。"""


def create_app() -> Any:
    from src.register import create_app as create_backend_app

    return create_backend_app()


def build_validated_permission_leaves(app: Any) -> list[dict[str, Any]]:
    from src.utils.permission_scanner import (
        build_validated_permission_leaves as build_backend_permission_leaves,
    )

    return build_backend_permission_leaves(app)


def _canonical_json_bytes(payload: Any, *, trailing_newline: bool) -> bytes:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return encoded + (b"\n" if trailing_newline else b"")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_relative_path(relative_path: str) -> None:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative_path:
        raise ReleaseProviderExportError(f"指纹输入路径必须为仓库相对 POSIX 路径: {relative_path!r}")


def _hash_input_set(repo_root: Path, relative_paths: list[str] | tuple[str, ...]) -> str:
    files: list[dict[str, str]] = []
    for relative_path in sorted(relative_paths):
        _validate_relative_path(relative_path)
        source = repo_root / relative_path
        if not source.is_file():
            raise ReleaseProviderExportError(f"指纹输入文件不存在: {relative_path}")
        files.append({"path": relative_path, "sha256": _sha256(source.read_bytes())})
    payload = {"kind": "wes.release.input-set.v1", "files": files}
    return _sha256(_canonical_json_bytes(payload, trailing_newline=False))


def _migration_paths(repo_root: Path) -> tuple[str, ...]:
    version_paths = tuple(
        path.relative_to(repo_root).as_posix()
        for path in sorted((repo_root / "migrations/versions").glob("*.py"))
        if path.is_file()
    )
    if not version_paths:
        raise ReleaseProviderExportError("未发现 Alembic migration versions")
    return ("alembic.ini", "migrations/env.py", *version_paths)


def _expected_schema_head(repo_root: Path) -> str:
    config = Config(str(repo_root / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise ReleaseProviderExportError(f"Alembic 必须且只能有一个 head，实际为: {sorted(heads)!r}")
    return heads[0]


def _validate_canonical_json_artifacts(artifacts: dict[str, bytes]) -> dict[str, Any]:
    decoded = {name: json.loads(payload) for name, payload in artifacts.items()}
    for name, payload in decoded.items():
        if artifacts[name] != _canonical_json_bytes(payload, trailing_newline=True):
            raise ReleaseProviderExportError(f"产物不是确定性 UTF-8 JSON: {name}")
    return decoded


def _validate_fingerprints(
    fingerprints: dict[str, Any],
    raw_artifacts: dict[str, bytes],
    *,
    raw_mismatch_message: str | None = None,
) -> None:
    expected_fields = {
        "kind",
        "provider_openapi_sha256",
        "provided_permissions_sha256",
        "migration_tree_sha256",
        "dependencies_sha256",
        "recipe_sha256",
        "expected_schema_head",
    }
    if set(fingerprints) != expected_fields or fingerprints.get("kind") != "wes.release.backend-fingerprints.v1":
        raise ReleaseProviderExportError("provider fingerprints schema 无效")
    for field in expected_fields - {"kind", "expected_schema_head"}:
        if not isinstance(fingerprints[field], str) or _SHA256_PATTERN.fullmatch(fingerprints[field]) is None:
            raise ReleaseProviderExportError(f"provider fingerprint 字段无效: {field}")
    schema_head = fingerprints["expected_schema_head"]
    if not isinstance(schema_head, str) or _SCHEMA_HEAD_PATTERN.fullmatch(schema_head) is None:
        raise ReleaseProviderExportError("provider fingerprint 字段无效: expected_schema_head")
    if fingerprints["provider_openapi_sha256"] != _sha256(raw_artifacts["provider-openapi.json"]):
        raise ReleaseProviderExportError(raw_mismatch_message or "OpenAPI raw bytes 指纹不匹配")
    if fingerprints["provided_permissions_sha256"] != _sha256(raw_artifacts["provided-permissions.json"]):
        raise ReleaseProviderExportError(raw_mismatch_message or "permission raw bytes 指纹不匹配")


def _validate_artifacts(artifacts: dict[str, bytes]) -> None:
    if set(artifacts) != set(_ARTIFACT_NAMES):
        raise ReleaseProviderExportError("发布能力产物集合不完整")
    decoded = _validate_canonical_json_artifacts(artifacts)
    _validate_fingerprints(decoded["provider-fingerprints.json"], artifacts)


def validate_release_provider_artifacts(
    artifact_dir: Path,
    *,
    expected: dict[str, str] | None = None,
    revision: str | None = None,
    source_tree: str | None = None,
) -> dict[str, str]:
    """Validate one exporter output directory at the image build boundary."""
    artifact_dir = artifact_dir.resolve()
    if not artifact_dir.is_dir():
        raise ReleaseProviderExportError(f"provider 产物目录不存在: {artifact_dir}")
    actual_names = {path.name for path in artifact_dir.iterdir() if path.is_file()}
    if (revision is not None or source_tree is not None) and (
        not isinstance(revision, str)
        or _GIT_OBJECT_PATTERN.fullmatch(revision) is None
        or not isinstance(source_tree, str)
        or _GIT_OBJECT_PATTERN.fullmatch(source_tree) is None
    ):
        raise ReleaseProviderExportError("镜像 Git 身份必须是 40 位 lowercase hex")
    if actual_names != set(_ARTIFACT_NAMES):
        raise ReleaseProviderExportError("provider 产物目录必须且只能包含三个 exporter 文件")
    artifacts = {name: (artifact_dir / name).read_bytes() for name in _ARTIFACT_NAMES}
    _validate_artifacts(artifacts)
    fingerprints = json.loads(artifacts["provider-fingerprints.json"])
    if expected is not None and fingerprints != expected:
        raise ReleaseProviderExportError("镜像 label 输入与 exporter 指纹不一致")
    return fingerprints


def _promote_directory(staged_dir: Path, out_dir: Path) -> None:
    if not out_dir.exists():
        staged_dir.replace(out_dir)
        return
    if not out_dir.is_dir():
        raise ReleaseProviderExportError(f"输出路径已存在且不是目录: {out_dir}")

    backup_dir = out_dir.parent / f".{out_dir.name}.backup-{uuid.uuid4().hex}"
    out_dir.replace(backup_dir)
    try:
        staged_dir.replace(out_dir)
    except BaseException:
        backup_dir.replace(out_dir)
        raise
    with contextlib.suppress(OSError):
        # staged_dir 已成为正式输出，promotion 是 commit point；保留可恢复 backup，不伪报导出失败。
        shutil.rmtree(backup_dir)


def export_release_provider(out_dir: Path, *, repo_root: Path = REPO_ROOT) -> None:
    repo_root = repo_root.resolve()
    out_dir = out_dir.resolve()
    app = create_app()
    openapi_bytes = _canonical_json_bytes(app.openapi(), trailing_newline=True)
    permissions = {
        "kind": "wes.release.provided-permissions.v1",
        "permissions": build_validated_permission_leaves(app),
    }
    permission_bytes = _canonical_json_bytes(permissions, trailing_newline=True)
    fingerprints = {
        "kind": "wes.release.backend-fingerprints.v1",
        "provider_openapi_sha256": _sha256(openapi_bytes),
        "provided_permissions_sha256": _sha256(permission_bytes),
        "migration_tree_sha256": _hash_input_set(repo_root, _migration_paths(repo_root)),
        "dependencies_sha256": _hash_input_set(repo_root, _DEPENDENCY_PATHS),
        "recipe_sha256": _hash_input_set(repo_root, _RECIPE_PATHS),
        "expected_schema_head": _expected_schema_head(repo_root),
    }
    artifacts = {
        "provider-openapi.json": openapi_bytes,
        "provided-permissions.json": permission_bytes,
        "provider-fingerprints.json": _canonical_json_bytes(fingerprints, trailing_newline=True),
    }
    _validate_artifacts(artifacts)

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staged_dir = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=out_dir.parent))
    try:
        for name in _ARTIFACT_NAMES:
            (staged_dir / name).write_bytes(artifacts[name])
        written = {name: (staged_dir / name).read_bytes() for name in _ARTIFACT_NAMES}
        _validate_artifacts(written)
        _promote_directory(staged_dir, out_dir)
    finally:
        if staged_dir.exists():
            shutil.rmtree(staged_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出后端发布能力合同")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    export_release_provider(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
