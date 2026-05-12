"""Celery worker container healthcheck.

Docker healthcheck should verify the worker process is alive without depending
on Celery remote-control replies. Remote control can be unavailable while the
worker process is still restarting under the dev autoreload wrapper.
"""

from __future__ import annotations

from pathlib import Path


def _read_cmdline_args(path: Path) -> list[str]:
    try:
        return [part.decode(errors="ignore") for part in path.read_bytes().split(b"\0") if part]
    except OSError:
        return []


def _is_celery_worker_command(args: list[str]) -> bool:
    arg_names = [Path(arg).name for arg in args]
    if "worker" not in args:
        return False
    if "inspect" in args and "ping" in args:
        return False
    return "celery" in arg_names or ("-m" in args and "celery" in args)


def has_celery_worker_process(proc_root: Path = Path("/proc")) -> bool:
    for cmdline_path in proc_root.glob("[0-9]*/cmdline"):
        if _is_celery_worker_command(_read_cmdline_args(cmdline_path)):
            return True
    return False


def main() -> int:
    return 0 if has_celery_worker_process() else 1


if __name__ == "__main__":
    raise SystemExit(main())
