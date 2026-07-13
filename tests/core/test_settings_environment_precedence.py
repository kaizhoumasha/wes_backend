"""Settings 进程环境优先级合同。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_process_environment_overrides_repository_dotenv_without_import_side_effects() -> None:
    expected = {
        "POSTGRES_HOST": "isolated-postgres.invalid",
        "POSTGRES_PORT": "6543",
        "POSTGRES_USER": "isolated_user",
        "POSTGRES_PASSWORD": "isolated_S3cure_Db_Secret_2026",
        "POSTGRES_DB": "isolated_database",
        "REDIS_HOST": "isolated-redis.invalid",
        "REDIS_PORT": "6388",
        "REDIS_PASSWORD": "isolated_R3dis_Secret_2026",
        "REDIS_DB": "9",
        "DATABASE_RUNTIME_ROLE": "cli",
        "DATABASE_POOL_SIZE": "1",
        "DATABASE_MAX_OVERFLOW": "0",
    }
    script = """
import json
from src.core.conf import settings

print(json.dumps({
    "POSTGRES_HOST": settings.POSTGRES_HOST,
    "POSTGRES_PORT": str(settings.POSTGRES_PORT),
    "POSTGRES_USER": settings.POSTGRES_USER,
    "POSTGRES_PASSWORD": settings.POSTGRES_PASSWORD,
    "POSTGRES_DB": settings.POSTGRES_DB,
    "REDIS_HOST": settings.REDIS_HOST,
    "REDIS_PORT": str(settings.REDIS_PORT),
    "REDIS_PASSWORD": settings.REDIS_PASSWORD,
    "REDIS_DB": str(settings.REDIS_DB),
}))
"""

    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**os.environ, **expected},
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"Settings 隔离导入子进程在 10s 内未退出；stdout={exc.stdout!r}, stderr={exc.stderr!r}")

    assert completed.returncode == 0, (
        "Settings 隔离导入子进程执行失败；"
        f"returncode={completed.returncode}, stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )
    actual = json.loads(completed.stdout.splitlines()[-1])
    assert actual == {key: value for key, value in expected.items() if key in actual}, (
        "conf.py 导入不得用仓库 .env 覆盖真实进程环境"
    )
