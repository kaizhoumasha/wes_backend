#!/usr/bin/env python3
"""部署前读取 live PostgreSQL 容量并校验 API/Celery 基础连接池预算。"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
from dataclasses import dataclass

import asyncpg

API_UVICORN_WORKERS = 4
API_DATABASE_POOL_SIZE = 5
CELERY_DATABASE_POOL_SIZE = 1
MINIMUM_CONNECTION_RESERVE = 10


class CapacityViolation(ValueError):
    """目标拓扑超过数据库连接预算或包含非法池配置。"""


@dataclass(frozen=True)
class CapacityPlan:
    api_replicas: int
    api_processes: int
    api_pool_size: int
    celery_replicas: int
    celery_processes: int
    celery_pool_size: int
    reserve: int = 10
    max_overflow: int = 0

    def __post_init__(self) -> None:
        numeric_values = (
            self.api_replicas,
            self.api_processes,
            self.api_pool_size,
            self.celery_replicas,
            self.celery_processes,
            self.celery_pool_size,
            self.reserve,
        )
        if any(value < 0 for value in numeric_values):
            raise CapacityViolation("capacity inputs must be non-negative")
        if self.reserve < MINIMUM_CONNECTION_RESERVE:
            raise CapacityViolation(f"reserve must be at least {MINIMUM_CONNECTION_RESERVE}")
        if self.api_replicas and self.api_processes != API_UVICORN_WORKERS:
            raise CapacityViolation(f"api_processes must equal {API_UVICORN_WORKERS}")
        if self.api_replicas and self.api_pool_size != API_DATABASE_POOL_SIZE:
            raise CapacityViolation(f"api_pool_size must equal {API_DATABASE_POOL_SIZE}")
        if self.celery_pool_size != 1:
            raise CapacityViolation("celery_pool_size must equal 1")
        if self.max_overflow != 0:
            raise CapacityViolation("max_overflow must equal 0")


@dataclass(frozen=True)
class CapacityResult:
    api_connections: int
    celery_connections: int
    application_connections: int
    available_connections: int


def calculate_capacity(plan: CapacityPlan, *, max_connections: int) -> CapacityResult:
    """计算基础池占用；CLI/迁移短连接由 reserve 覆盖，不重复计入。"""
    available = max_connections - plan.reserve
    api_connections = plan.api_replicas * plan.api_processes * plan.api_pool_size
    celery_connections = plan.celery_replicas * plan.celery_processes * plan.celery_pool_size
    application_connections = api_connections + celery_connections
    if available < 0 or application_connections > available:
        raise CapacityViolation(
            f"application connection budget {application_connections} exceeds available {available} "
            f"(max_connections={max_connections}, reserve={plan.reserve})"
        )
    return CapacityResult(api_connections, celery_connections, application_connections, available)


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _parse_scale(values: list[str]) -> dict[str, int]:
    scales: dict[str, int] = {}
    for value in values:
        service, separator, count = value.partition("=")
        if not separator or service not in {"api", "celery_worker"}:
            raise CapacityViolation(f"unsupported scale target: {value}")
        scales[service] = int(count)
    return scales


def _runtime_constant(name: str, expected: int) -> int:
    """拒绝与 Dockerfile/Compose 唯一运行拓扑不一致的伪预算覆盖。"""
    try:
        configured = _env_int(name, expected)
    except ValueError as exc:
        raise CapacityViolation(f"{name} must be the deployed integer value {expected}") from exc
    if configured != expected:
        raise CapacityViolation(f"{name}={configured} disagrees with deployed runtime value {expected}")
    return expected


def build_capacity_plan(
    *,
    services: set[str],
    scales: dict[str, int],
    reserve: int | None = None,
) -> CapacityPlan:
    """从可扩缩容维度和固定镜像/Compose 维度构造唯一容量计划。"""
    api_processes = _runtime_constant("API_UVICORN_WORKERS", API_UVICORN_WORKERS)
    api_pool_size = _runtime_constant("API_DATABASE_POOL_SIZE", API_DATABASE_POOL_SIZE)
    celery_pool_size = _runtime_constant("CELERY_DATABASE_POOL_SIZE", CELERY_DATABASE_POOL_SIZE)
    return CapacityPlan(
        api_replicas=scales.get("api", _env_int("API_REPLICAS", 1)) if "api" in services else 0,
        api_processes=api_processes,
        api_pool_size=api_pool_size,
        celery_replicas=(
            scales.get("celery_worker", _env_int("CELERY_WORKER_REPLICAS", 4)) if "celery_worker" in services else 0
        ),
        celery_processes=_env_int("CELERY_CONCURRENCY", 4),
        celery_pool_size=celery_pool_size,
        reserve=_env_int("DATABASE_CONNECTION_RESERVE", MINIMUM_CONNECTION_RESERVE) if reserve is None else reserve,
        max_overflow=_env_int("DATABASE_MAX_OVERFLOW", 0),
    )


async def _read_max_connections() -> int:
    application_name = f"{os.getenv('ENV', 'unknown')}:cli:{socket.gethostname()}:{os.getpid()}:capacity"
    connection = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=_env_int("POSTGRES_PORT", 5432),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
        server_settings={"application_name": application_name[-63:]},
    )
    try:
        return int(await connection.fetchval("SHOW max_connections"))
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", default="api,celery_worker")
    parser.add_argument("--scale", action="append", default=[])
    parser.add_argument("--max-connections", type=int)
    parser.add_argument("--reserve", type=int, default=_env_int("DATABASE_CONNECTION_RESERVE", 10))
    args = parser.parse_args()
    services = {value.strip() for value in args.services.split(",") if value.strip()}
    if not services <= {"api", "celery_worker"}:
        raise CapacityViolation(f"unsupported services: {sorted(services)}")
    scales = _parse_scale(args.scale)
    plan = build_capacity_plan(services=services, scales=scales, reserve=args.reserve)
    max_connections = args.max_connections if args.max_connections is not None else asyncio.run(_read_max_connections())
    result = calculate_capacity(plan, max_connections=max_connections)
    print(
        "capacity guard passed: "
        f"api={result.api_connections}, celery={result.celery_connections}, "
        f"application={result.application_connections}, available={result.available_connections}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
