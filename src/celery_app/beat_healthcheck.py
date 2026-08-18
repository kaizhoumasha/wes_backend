"""Celery beat 子进程健康检查。"""

from __future__ import annotations

from pathlib import Path


def _read_cmdline_args(path: Path) -> list[str]:
    try:
        return [part.decode(errors="ignore") for part in path.read_bytes().split(b"\0") if part]
    except OSError:
        return []


def _is_celery_beat_command(args: list[str]) -> bool:
    arg_names = [Path(arg).name for arg in args]
    if "beat" not in args:
        return False
    return "celery" in arg_names or ("-m" in args and "celery" in args)


def has_celery_beat_process(proc_root: Path = Path("/proc")) -> bool:
    return any(
        _is_celery_beat_command(_read_cmdline_args(cmdline_path)) for cmdline_path in proc_root.glob("[0-9]*/cmdline")
    )


def main() -> int:
    return 0 if has_celery_beat_process() else 1


if __name__ == "__main__":
    raise SystemExit(main())
