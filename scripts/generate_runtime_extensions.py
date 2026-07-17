"""生成 Workline Plugin 与 System Capability 的确定性静态索引。"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.runtime.system_capabilities.index_builder import SystemCapabilityIndexBuilder  # noqa: E402
from src.app.runtime.workline_plugins.index_builder import WorklinePluginIndexBuilder  # noqa: E402
from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort  # noqa: E402

PLUGIN_ROOT = REPO_ROOT / "src/app/runtime/workline_plugins"
SYSTEM_ROOT = REPO_ROOT / "src/app/runtime/system_capabilities"
DEFAULT_PLUGIN_OUTPUT = PLUGIN_ROOT / "generated_index.py"
DEFAULT_SYSTEM_OUTPUT = SYSTEM_ROOT / "generated_index.py"
# Port catalog 是构建期显式 allowlist；后续新增真实 Port 时必须在同一提交登记。
SYSTEM_CAPABILITY_PORT_CATALOG: tuple[type[object], ...] = (WmsInventoryQueryPort,)
SYSTEM_CAPABILITY_ADMISSION_CATALOG = (
    "provider-contract",
    "runtime",
    "wms.2026-07-06.material-flow.sandbox",
)


def _filesystem_names_collide(first: Path, second: Path) -> bool:
    first_key = unicodedata.normalize("NFC", str(first)).casefold()
    second_key = unicodedata.normalize("NFC", str(second)).casefold()
    if first_key != second_key:
        return False

    common_path = Path(os.path.commonpath((first, second)))
    while not common_path.is_dir():
        common_path = common_path.parent
    with tempfile.TemporaryDirectory(prefix=".runtime-index-path-probe-", dir=common_path) as probe_name:
        probe_root = Path(probe_name)
        first_probe = probe_root / first.relative_to(common_path)
        second_probe = probe_root / second.relative_to(common_path)
        first_probe.parent.mkdir(parents=True, exist_ok=True)
        first_probe.touch(exist_ok=False)
        try:
            second_probe.parent.mkdir(parents=True, exist_ok=True)
            second_probe.touch(exist_ok=False)
        except FileExistsError:
            return True
    return False


def _ensure_distinct_destinations(plugin_output: Path, system_output: Path) -> None:
    plugin_resolved = plugin_output.resolve(strict=False)
    system_resolved = system_output.resolve(strict=False)
    same_inode = plugin_output.exists() and system_output.exists() and plugin_output.samefile(system_output)
    if plugin_resolved == system_resolved or same_inode or _filesystem_names_collide(plugin_resolved, system_resolved):
        raise ValueError("plugin_output and system_output must be distinct destinations")


def _default_output_mode() -> int:
    current_umask = os.umask(0)
    os.umask(current_umask)
    return 0o666 & ~current_umask


def _stage_generated_file(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    output_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else _default_output_mode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            os.fchmod(handle.fileno(), output_mode)
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _replace_one(temporary_path: Path, destination: Path) -> None:
    temporary_path.replace(destination)


def _replace_generated_files(replacements: tuple[tuple[Path, Path], ...]) -> None:
    """两个目标作为一组替换；任一失败时恢复已替换目标。"""

    previous = {
        destination: destination.read_text(encoding="utf-8") if destination.exists() else None
        for _temporary_path, destination in replacements
    }
    replaced: list[Path] = []
    try:
        for temporary_path, destination in replacements:
            _replace_one(temporary_path, destination)
            replaced.append(destination)
    except BaseException:
        for destination in reversed(replaced):
            old_source = previous[destination]
            if old_source is None:
                destination.unlink(missing_ok=True)
                continue
            rollback_path = _stage_generated_file(destination, old_source)
            try:
                rollback_path.replace(destination)
            finally:
                rollback_path.unlink(missing_ok=True)
        raise


def _is_current(path: Path, expected: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == expected
    except FileNotFoundError:
        return False


def generate(*, plugin_output: Path, system_output: Path, check: bool) -> int:
    _ensure_distinct_destinations(plugin_output, system_output)
    system_builder = SystemCapabilityIndexBuilder(
        known_ports=SYSTEM_CAPABILITY_PORT_CATALOG,
        known_admissions=SYSTEM_CAPABILITY_ADMISSION_CATALOG,
    )
    system_sources = system_builder.discover(
        root=SYSTEM_ROOT,
        package="src.app.runtime.system_capabilities",
    )
    system_index = system_builder.build(system_sources)
    capability_modes = {
        (source.definition.capability_key, source.definition.contract_version): source.definition.mode
        for source in system_sources
    }

    plugin_builder = WorklinePluginIndexBuilder(capability_modes=capability_modes)
    plugin_sources = plugin_builder.discover(
        root=PLUGIN_ROOT,
        package="src.app.runtime.workline_plugins",
    )
    plugin_index = plugin_builder.build(plugin_sources)

    print(f"workline_plugins: count={len(plugin_index.identities)} digest={plugin_index.digest}")
    print(f"system_capabilities: count={len(system_index.identities)} digest={system_index.digest}")

    drifted = tuple(
        path
        for path, source in (
            (plugin_output, plugin_index.source),
            (system_output, system_index.source),
        )
        if not _is_current(path, source)
    )
    if check:
        if drifted:
            for path in drifted:
                print(f"generated index drift: {path}", file=sys.stderr)
            return 1
        return 0

    staged: list[tuple[Path, Path]] = []
    try:
        for destination, source in (
            (plugin_output, plugin_index.source),
            (system_output, system_index.source),
        ):
            if destination in drifted:
                staged.append((_stage_generated_file(destination, source), destination))
        _replace_generated_files(tuple(staged))
    finally:
        for temporary_path, _destination in staged:
            temporary_path.unlink(missing_ok=True)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查生成文件是否漂移")
    parser.add_argument("--plugin-output", type=Path, default=DEFAULT_PLUGIN_OUTPUT)
    parser.add_argument("--system-output", type=Path, default=DEFAULT_SYSTEM_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return generate(
        plugin_output=args.plugin_output,
        system_output=args.system_output,
        check=args.check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
