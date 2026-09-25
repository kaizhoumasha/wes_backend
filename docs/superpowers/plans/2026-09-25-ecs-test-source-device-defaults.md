# ECS_TEST 来源设备默认值 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Device 加一个可空的 ECS_TEST 默认值（`target_device_code`/`task_type`/`params`），联调界面选中一台 ECS_TEST 来源设备时可以读到常用配置、编辑、清除；保存默认值本身不驱动任何设备行为，也不改变 WorkLine 的生效配置或已合并的 ECS_TEST 执行路径。

**Architecture:** 在 `Device`（表类，不是 `DeviceEditableBase`）新增可空 JSON 字段，配一条独立的 schema migration；新增一对专用端点（`GET/PUT /devices/{device_code}/ecs-test-default`），完全绕开通用 `DeviceCreate`/`DeviceUpdate` 和现有的 WorkLine 活动线配置门禁；写入路径复用现有 `parse_ecs_test_rules` 做结构校验，复用现有 `DeviceRepository.get_by_device_code_for_update` 做行锁读取。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy 2.0、PostgreSQL、Alembic、pytest。

**Spec:** `docs/superpowers/specs/2026-09-25-workline-debug-instruction-drafts-design.md`

## Global Constraints

- 不修改 `_process_event()`、`_start_ecs_test()`、`create_run()`、`_reject_active_configuration_update()` 的任何现有行为；这几处已经在 PR #276 合并、跑过真实 Postgres 集成测试和一次外部代码评审。
- `ecs_test_default_json` 不加入 `DeviceCreate`、`DeviceUpdate`、`DeviceResponse`；只有本计划新增的专用端点可读写。
- 保存默认值不要求 `Device.work_line_id` 非空、不要求 WorkLine 停用、不要求 WorkLine 处于 `ECS_TEST` 模式；这是纯粹的设备级预填数据。
- `PUT` 不要求客户端提交 `Device.version`；服务端仍在设备行锁下写入并推进已有的乐观锁版本号。
- 新增 Alembic 迁移必须通过 `uv run alembic revision -m "<message>"` 生成，不手写 revision id。
- 每个任务完成后运行其 FAST 测试；涉及真实 Postgres 行为的部分（行锁、版本递增、迁移本身）必须用 `tests/integration/` 下的真实数据库测试验证，跳过不算通过。

## Review Focus

1. **保存默认值时目标设备编码或 `task_type` 是空字符串、`params` 不是对象**——`parse_ecs_test_rules` 应该拒绝，返回 4xx，不写入部分数据；见 Task 2 Step 1、Task 3 Step 1。
2. **`PUT` 请求体里混入 `source_device_code` 或未知字段**——`EcsTestDefaultUpdateRequest` 用 `extra="forbid"`，Pydantic 直接拒绝，不需要业务层再判断；见 Task 3 Step 1。
3. **目标 `device_code` 不存在**——`GET`/`PUT` 都必须返回 404，不能返回一个"看起来正常但设备是 None"的响应；见 Task 3 Step 1。
4. **同一设备两个请求并发保存默认值**——后写入者覆盖先写入者，`device_version` 正确递增两次，不出现丢失更新或版本号错乱；见 Task 4 Step 1（真实 Postgres 并发测试）。
5. **保存默认值不能触碰 WorkLine 的生效配置**——同一事务/请求之外，`WorkLine.runtime_config_json.ecs_test_rules` 和任何在途 `DeviceCommand` 必须保持不变；见 Task 1 Step 4 的 `test_save_does_not_touch_workline_ecs_test_rules`。
6. **保存默认值推进的 `Device.version` 必须真正参与既有乐观锁**——如果有人在保存默认值之前已经拿到旧版本号准备走通用 `PUT /devices/{id}` 更新静态主数据，那次更新必须按现有乐观锁规则被拒绝，不能因为新端点绕开了 `DeviceUpdate` 就顺带绕开了版本校验本身；见 Task 1 Step 4 的 `test_save_advances_version_that_generic_update_still_enforces`。

---

## 变更面与文件职责

| 文件 | 职责 |
| --- | --- |
| `src/app/device/models/device.py` | `Device`（表类）新增 `ecs_test_default_json: dict[str, Any] \| None` |
| `migrations/versions/<new>_add_device_ecs_test_default.py` | 新增可空 JSON 列 |
| `src/app/device/repositories/device_repository.py` | 新增 `save_ecs_test_default()`，复用既有 `get_by_device_code_for_update` |
| `src/app/device/domain/ecs_test_default.py`（新增） | 纯函数校验器，复用 `parse_ecs_test_rules` |
| `src/app/device/services/ecs_test_default_service.py`（新增） | `EcsTestDefaultService.get()`/`save()`，负责事务提交与缓存失效 |
| `src/app/device/v1/ecs_test_default.py`（新增） | `GET`/`PUT` 路由、请求/响应 schema |
| `src/app/device/v1/__init__.py` | 挂载新路由 |
| `docs/architecture/heavy-test-impact.toml` | 新文件与新迁移的精确 mapping |

## Task 1：Device 模型字段、Migration、Repository 写入方法

**Files:**
- Modify: `src/app/device/models/device.py`
- Create: `migrations/versions/<alembic-generated>_add_device_ecs_test_default.py`
- Modify: `src/app/device/repositories/device_repository.py`
- Test: `tests/integration/device_command/test_ecs_test_default_postgresql.py`（新增）

**Interfaces:**
- Produces: `Device.ecs_test_default_json: dict[str, Any] | None`；`DeviceRepository.save_ecs_test_default(db: AsyncSession, device_code: str, default: dict[str, Any] | None) -> Device | None`（设备不存在返回 `None`，否则替换字段、推进 `version`、`flush` 后返回该 `Device`）。

这一步的行锁、JSON 列持久化、版本递增行为只有真实 Postgres 能验证；FAST 测试在这里没有实质意义，直接写 Postgres 集成测试作为本任务的 RED/GREEN 循环。

- [ ] **Step 1 — 加字段：**

在 `src/app/device/models/device.py` 的 `Device` 类（不是 `DeviceBase`，也不是 `DeviceEditableBase`——这两个会分别泄漏进 `DeviceResponse` 和 `DeviceCreate`/`DeviceUpdate`）里加：

```python
class Device(DeviceBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """设备静态主数据；实时状态只来自 ECS observation。"""

    __tablename__: ClassVar[Literal["devices"]] = "devices"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_devices_device_code_deleted",
            "device_code",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )

    ecs_test_default_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    work_line: WorkLine = Relationship(sa_relationship_kwargs={"lazy": "selectin"})
    ...
```

（`Any`、`JSON`、`Column`、`Field` 在这个文件里已经导入，不需要新增 import。）

- [ ] **Step 2 — 生成 migration：**

```bash
uv run alembic revision -m "add device ecs_test default"
```

打开生成的文件，把 `upgrade`/`downgrade` 写成：

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "devices",
        sa.Column("ecs_test_default_json", sa.JSON(), nullable=True),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("devices", "ecs_test_default_json", schema="wes_biz")
```

跑一次 `uv run ruff format <生成的文件路径>` 让格式跟其它迁移一致。

- [ ] **Step 3 — 加 Repository 方法：**

在 `src/app/device/repositories/device_repository.py` 的 `DeviceRepository` 类里，紧跟在 `get_by_device_code_for_update` 后面加：

```python
    async def save_ecs_test_default(
        self,
        db: AsyncSession,
        device_code: str,
        default: dict[str, Any] | None,
    ) -> Device | None:
        """在设备行锁下替换 ecs_test_default_json 并推进版本；设备不存在返回 None。"""

        device = await self.get_by_device_code_for_update(db, device_code)
        if device is None:
            return None
        device.ecs_test_default_json = default
        device.increment_version()
        await db.flush()
        return device
```

- [ ] **Step 4 — RED：写真实 Postgres 测试**

新建 `tests/integration/device_command/test_ecs_test_default_postgresql.py`：

```python
"""Device.ecs_test_default_json 的真实持久化与并发行为。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

from src.app.device.models.device import Device
from src.app.device.repositories.device_repository import DeviceRepository
from src.app.workline.models.workline import LineType, WorkLine, WorkLineRunMode

pytestmark = pytest.mark.asyncio


async def _seed_device(db, *, workline_id: int | None = None) -> str:
    device_code = f"ECS-DEFAULT-{uuid4().hex[:12]}"
    db.add(
        Device(
            device_code=device_code,
            device_name="ECS default test device",
            work_line_id=workline_id,
        )
    )
    await db.flush()
    return device_code


async def test_save_persists_default_and_advances_version(integration_session_factory) -> None:
    repository = DeviceRepository()
    device_code = None
    try:
        async with integration_session_factory.begin() as db:
            device_code = await _seed_device(db)

        async with integration_session_factory.begin() as db:
            saved = await repository.save_ecs_test_default(
                db,
                device_code,
                {"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}},
            )
            assert saved is not None
            version_after_save = saved.version

        async with integration_session_factory() as db:
            persisted = await repository.get_by_device_code(db, device_code)
            assert persisted.ecs_test_default_json == {
                "target_device_code": "TARGET-1",
                "task_type": "MOVE_FORWARD",
                "params": {},
            }
            assert persisted.version == version_after_save
    finally:
        if device_code is not None:
            async with integration_session_factory.begin() as db:
                await db.execute(delete(Device).where(Device.device_code == device_code))


async def test_save_with_none_clears_default(integration_session_factory) -> None:
    repository = DeviceRepository()
    device_code = None
    try:
        async with integration_session_factory.begin() as db:
            device_code = await _seed_device(db)
            await repository.save_ecs_test_default(
                db, device_code, {"target_device_code": "T", "task_type": "X", "params": {}}
            )

        async with integration_session_factory.begin() as db:
            await repository.save_ecs_test_default(db, device_code, None)

        async with integration_session_factory() as db:
            persisted = await repository.get_by_device_code(db, device_code)
            assert persisted.ecs_test_default_json is None
    finally:
        if device_code is not None:
            async with integration_session_factory.begin() as db:
                await db.execute(delete(Device).where(Device.device_code == device_code))


async def test_save_missing_device_returns_none(integration_session_factory) -> None:
    repository = DeviceRepository()
    async with integration_session_factory.begin() as db:
        result = await repository.save_ecs_test_default(
            db, f"NO-SUCH-DEVICE-{uuid4().hex}", {"target_device_code": "T", "task_type": "X", "params": {}}
        )
    assert result is None


async def test_save_isolates_defaults_per_device(integration_session_factory) -> None:
    """两台设备各自保存默认值，互不覆盖、互不可见。"""

    repository = DeviceRepository()
    device_a = device_b = None
    try:
        async with integration_session_factory.begin() as db:
            device_a = await _seed_device(db)
            device_b = await _seed_device(db)
            await repository.save_ecs_test_default(
                db, device_a, {"target_device_code": "A-TARGET", "task_type": "X", "params": {}}
            )
            await repository.save_ecs_test_default(
                db, device_b, {"target_device_code": "B-TARGET", "task_type": "Y", "params": {}}
            )

        async with integration_session_factory() as db:
            persisted_a = await repository.get_by_device_code(db, device_a)
            persisted_b = await repository.get_by_device_code(db, device_b)
            assert persisted_a.ecs_test_default_json["target_device_code"] == "A-TARGET"
            assert persisted_b.ecs_test_default_json["target_device_code"] == "B-TARGET"
    finally:
        async with integration_session_factory.begin() as db:
            for device_code in (device_a, device_b):
                if device_code is not None:
                    await db.execute(delete(Device).where(Device.device_code == device_code))


async def test_save_allows_editing_while_workline_is_active(integration_session_factory) -> None:
    """设备处于活动线时仍能保存默认值——这是本设计区别于 ECS_TEST 生效配置门禁的核心点。"""

    repository = DeviceRepository()
    device_code = None
    line_id = None
    try:
        async with integration_session_factory.begin() as db:
            line = WorkLine(
                line_code=f"ECS-DEFAULT-LINE-{uuid4().hex[:12]}",
                line_name="ECS default active line",
                line_type=LineType.AUTO,
                run_mode=WorkLineRunMode.ECS_TEST,
                is_active=True,
            )
            db.add(line)
            await db.flush()
            line_id = line.id
            device_code = await _seed_device(db, workline_id=line_id)

        async with integration_session_factory.begin() as db:
            saved = await repository.save_ecs_test_default(
                db, device_code, {"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}}
            )
            assert saved is not None
    finally:
        async with integration_session_factory.begin() as db:
            if device_code is not None:
                await db.execute(delete(Device).where(Device.device_code == device_code))
            if line_id is not None:
                await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


async def test_save_does_not_touch_workline_ecs_test_rules(integration_session_factory) -> None:
    """保存设备默认值绝不能改动 WorkLine 自己的生效 `ecs_test_rules`。"""

    repository = DeviceRepository()
    device_code = None
    line_id = None
    original_config = {
        "ecs_test_rules": [
            {"source_device_code": "OTHER-SOURCE", "target_device_code": "OTHER-TARGET", "task_type": "X", "params": {}}
        ]
    }
    try:
        async with integration_session_factory.begin() as db:
            line = WorkLine(
                line_code=f"ECS-DEFAULT-LINE-{uuid4().hex[:12]}",
                line_name="ECS default rules line",
                line_type=LineType.AUTO,
                run_mode=WorkLineRunMode.ECS_TEST,
                is_active=True,
                runtime_config_json=original_config,
            )
            db.add(line)
            await db.flush()
            line_id = line.id
            device_code = await _seed_device(db, workline_id=line_id)

        async with integration_session_factory.begin() as db:
            await repository.save_ecs_test_default(
                db, device_code, {"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}}
            )

        async with integration_session_factory() as db:
            persisted_line = await db.get(WorkLine, line_id)
            assert persisted_line.runtime_config_json == original_config
    finally:
        async with integration_session_factory.begin() as db:
            if device_code is not None:
                await db.execute(delete(Device).where(Device.device_code == device_code))
            if line_id is not None:
                await db.execute(delete(WorkLine).where(WorkLine.id == line_id))


async def test_save_advances_version_that_generic_update_still_enforces(integration_session_factory) -> None:
    """保存默认值推进的版本号必须真正参与既有乐观锁：拿着旧版本号走通用更新接口必须被拒绝。"""

    from src.app.device.services.device_service import DeviceService
    from src.core.exceptions import OptimisticLockException

    repository = DeviceRepository()
    device_service = DeviceService()
    device_code = None
    try:
        async with integration_session_factory.begin() as db:
            device_code = await _seed_device(db)
            seeded = await repository.get_by_device_code(db, device_code)
            device_id = seeded.id
            stale_version = seeded.version

        async with integration_session_factory.begin() as db:
            await repository.save_ecs_test_default(
                db, device_code, {"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}}
            )

        async with integration_session_factory.begin() as db:
            with pytest.raises(OptimisticLockException):
                await device_service.update(
                    db, device_id, {"version": stale_version, "device_name": "Renamed by stale caller"}
                )
    finally:
        if device_code is not None:
            async with integration_session_factory.begin() as db:
                await db.execute(delete(Device).where(Device.device_code == device_code))


async def test_concurrent_saves_serialize_and_advance_version_twice(integration_session_factory) -> None:
    """两个并发请求先后保存同一设备的默认值：不能丢失更新，版本号必须递增两次。"""

    repository = DeviceRepository()
    device_code = None
    held, release = asyncio.Event(), asyncio.Event()
    pids: dict[str, int] = {}
    try:
        async with integration_session_factory.begin() as db:
            device_code = await _seed_device(db)

        async def holder() -> None:
            async with integration_session_factory.begin() as db:
                await repository.save_ecs_test_default(
                    db, device_code, {"target_device_code": "FIRST", "task_type": "X", "params": {}}
                )
                pids["holder"] = await db.scalar(text("SELECT pg_backend_pid()"))
                held.set()
                await release.wait()

        first = asyncio.create_task(holder())
        await asyncio.wait_for(held.wait(), 5)

        async with integration_session_factory.begin() as second_db:
            waiter_pid = await second_db.scalar(text("SELECT pg_backend_pid()"))
            second_task = asyncio.create_task(
                repository.save_ecs_test_default(
                    second_db, device_code, {"target_device_code": "SECOND", "task_type": "Y", "params": {}}
                )
            )
            async with asyncio.timeout(5), integration_session_factory() as observer:
                while True:
                    blocking = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter_pid})
                    if pids["holder"] in (blocking or []):
                        break
                    await asyncio.sleep(0.01)
            release.set()
            await asyncio.wait_for(first, 5)
            await asyncio.wait_for(second_task, 5)

        async with integration_session_factory() as db:
            persisted = await repository.get_by_device_code(db, device_code)
            assert persisted.ecs_test_default_json == {"target_device_code": "SECOND", "task_type": "Y", "params": {}}
            assert persisted.version >= 2
    finally:
        release.set()
        if device_code is not None:
            async with integration_session_factory.begin() as db:
                await db.execute(delete(Device).where(Device.device_code == device_code))
```

- [ ] **Step 5 — verify RED：** `RUN_WORKLINE_INTEGRATION=1 ALLOW_SHARED_DEV_DB_INTEGRATION=1 INTEGRATION_DATABASE_URL=<...> uv run pytest tests/integration/device_command/test_ecs_test_default_postgresql.py -q`。在完成 Step 1-3 之前跑这个文件应该因为 `AttributeError: 'Device' object has no attribute 'ecs_test_default_json'` 或 `no such column` 失败；如果这几步已经做完再补的这个文件，就先临时 `git stash` 掉 Step 1-3 的改动验证 RED，再 `git stash pop`。
- [ ] **Step 6 — GREEN：** 完成 Step 1-3 后重跑同一条命令，8 个测试全部通过。
- [ ] **Step 7 — 迁移链回归：** 在独立干净的临时 Postgres 库跑 `uv run alembic upgrade head`，确认从 base 到 head 无报错；跑 `uv run alembic downgrade -1` 再 `upgrade head`，确认新迁移可逆。
- [ ] **Step 8 — Commit：**

```bash
git add src/app/device/models/device.py migrations/versions/*_add_device_ecs_test_default.py \
  src/app/device/repositories/device_repository.py \
  tests/integration/device_command/test_ecs_test_default_postgresql.py
git commit -m "feat(device): 新增 ECS_TEST 默认值字段与仓库写入方法"
```

## Task 2：结构校验与 Service 层

**Files:**
- Create: `src/app/device/domain/ecs_test_default.py`
- Create: `src/app/device/services/ecs_test_default_service.py`
- Test: `tests/device/test_ecs_test_default_service.py`（新增）

**Interfaces:**
- Consumes: Task 1 的 `DeviceRepository.save_ecs_test_default`；`src.app.workline.domain.ecs_test.parse_ecs_test_rules`；`DeviceService.invalidate_cache`（继承自 `BaseService`，已存在）。
- Produces: `validate_ecs_test_default(source_device_code: str, target_device_code: str, task_type: str, params: dict[str, object]) -> None`（校验失败抛 `ValueError`）；`EcsTestDefaultSnapshot`（`dataclass`，字段：`device_id: int`、`device_code: str`、`device_version: int`、`default: dict[str, object] | None`）；`EcsTestDefaultService.get(db, device_code) -> EcsTestDefaultSnapshot | None`；`EcsTestDefaultService.save(db, device_code, default, *, cache=None) -> EcsTestDefaultSnapshot | None`（设备不存在两者都返回 `None`）；模块级单例 `ecs_test_default_service`。

- [ ] **Step 1 — RED：** 新建 `tests/device/test_ecs_test_default_service.py`：

```python
"""ECS_TEST 默认值 Service 的校验与缓存失效边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.app.device.domain.ecs_test_default import validate_ecs_test_default
from src.app.device.services.ecs_test_default_service import EcsTestDefaultService


@dataclass
class _FakeDevice:
    id: int
    device_code: str
    version: int
    ecs_test_default_json: dict[str, Any] | None


class _FakeDeviceRepository:
    def __init__(self, device: _FakeDevice | None) -> None:
        self.device = device
        self.save_calls: list[dict[str, Any] | None] = []

    async def get_by_device_code(self, _db: object, device_code: str) -> _FakeDevice | None:
        if self.device is not None and self.device.device_code == device_code:
            return self.device
        return None

    async def save_ecs_test_default(
        self, _db: object, device_code: str, default: dict[str, Any] | None
    ) -> _FakeDevice | None:
        self.save_calls.append(default)
        if self.device is None or self.device.device_code != device_code:
            return None
        self.device.ecs_test_default_json = default
        self.device.version += 1
        return self.device


class _FakeDeviceService:
    def __init__(self) -> None:
        self.invalidated: list[int] = []

    async def invalidate_cache(self, _cache: object, device_id: int) -> None:
        self.invalidated.append(device_id)


def test_validate_rejects_empty_task_type() -> None:
    with pytest.raises(ValueError, match="task_type"):
        validate_ecs_test_default(
            source_device_code="SCAN-1", target_device_code="TARGET-1", task_type="", params={}
        )


def test_validate_rejects_non_dict_params() -> None:
    with pytest.raises(ValueError, match="params"):
        validate_ecs_test_default(
            source_device_code="SCAN-1",
            target_device_code="TARGET-1",
            task_type="MOVE_FORWARD",
            params="not-a-dict",  # type: ignore[arg-type]
        )


def test_validate_accepts_well_formed_default() -> None:
    validate_ecs_test_default(
        source_device_code="SCAN-1", target_device_code="TARGET-1", task_type="MOVE_FORWARD", params={"a": 1}
    )


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_device() -> None:
    service = EcsTestDefaultService(device_repository=_FakeDeviceRepository(None), device_service=_FakeDeviceService())
    assert await service.get(object(), "MISSING") is None


@pytest.mark.asyncio
async def test_get_returns_snapshot_with_stored_default() -> None:
    device = _FakeDevice(id=9, device_code="SCAN-1", version=3, ecs_test_default_json={"target_device_code": "T"})
    service = EcsTestDefaultService(device_repository=_FakeDeviceRepository(device), device_service=_FakeDeviceService())

    snapshot = await service.get(object(), "SCAN-1")

    assert snapshot is not None
    assert (snapshot.device_id, snapshot.device_code, snapshot.device_version, snapshot.default) == (
        9,
        "SCAN-1",
        3,
        {"target_device_code": "T"},
    )


@pytest.mark.asyncio
async def test_save_rejects_invalid_default_without_calling_repository() -> None:
    device = _FakeDevice(id=9, device_code="SCAN-1", version=3, ecs_test_default_json=None)
    repository = _FakeDeviceRepository(device)
    service = EcsTestDefaultService(device_repository=repository, device_service=_FakeDeviceService())

    with pytest.raises(ValueError, match="task_type"):
        await service.save(object(), "SCAN-1", {"target_device_code": "T", "task_type": "", "params": {}})

    assert repository.save_calls == []


@pytest.mark.asyncio
async def test_save_persists_and_invalidates_cache() -> None:
    device = _FakeDevice(id=9, device_code="SCAN-1", version=3, ecs_test_default_json=None)
    repository = _FakeDeviceRepository(device)
    device_service = _FakeDeviceService()
    service = EcsTestDefaultService(device_repository=repository, device_service=device_service)

    snapshot = await service.save(
        object(), "SCAN-1", {"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}}, cache=object()
    )

    assert snapshot is not None
    assert snapshot.default == {"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}}
    assert snapshot.device_version == 4
    assert device_service.invalidated == [9]


@pytest.mark.asyncio
async def test_save_none_clears_default_without_validation() -> None:
    device = _FakeDevice(id=9, device_code="SCAN-1", version=3, ecs_test_default_json={"target_device_code": "T"})
    repository = _FakeDeviceRepository(device)
    service = EcsTestDefaultService(device_repository=repository, device_service=_FakeDeviceService())

    snapshot = await service.save(object(), "SCAN-1", None)

    assert snapshot is not None
    assert snapshot.default is None
    assert repository.save_calls == [None]


@pytest.mark.asyncio
async def test_save_missing_device_returns_none() -> None:
    service = EcsTestDefaultService(device_repository=_FakeDeviceRepository(None), device_service=_FakeDeviceService())

    result = await service.save(
        object(), "MISSING", {"target_device_code": "T", "task_type": "X", "params": {}}
    )

    assert result is None
```

- [ ] **Step 2 — verify RED：** `uv run pytest tests/device/test_ecs_test_default_service.py -q` — 应该因为 `src.app.device.domain.ecs_test_default` 和 `src.app.device.services.ecs_test_default_service` 都不存在而报 `ModuleNotFoundError`。
- [ ] **Step 3 — 写 `src/app/device/domain/ecs_test_default.py`：**

```python
"""设备 ECS_TEST 默认值的结构校验。"""

from __future__ import annotations

from typing import Any

from src.app.workline.domain.ecs_test import parse_ecs_test_rules


def validate_ecs_test_default(
    *,
    source_device_code: str,
    target_device_code: str,
    task_type: str,
    params: dict[str, Any],
) -> None:
    """复用 `parse_ecs_test_rules` 的结构校验；`source_device_code` 由设备身份合成，不接受外部输入。"""

    parse_ecs_test_rules(
        {
            "ecs_test_rules": [
                {
                    "source_device_code": source_device_code,
                    "target_device_code": target_device_code,
                    "task_type": task_type,
                    "params": params,
                }
            ]
        }
    )


__all__ = ["validate_ecs_test_default"]
```

- [ ] **Step 4 — 写 `src/app/device/services/ecs_test_default_service.py`：**

```python
"""设备 ECS_TEST 默认值读写 Service。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.device.domain.ecs_test_default import validate_ecs_test_default
from src.app.device.repositories.device_repository import DeviceRepository, device_repository
from src.app.device.services.device_service import DeviceService, device_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class EcsTestDefaultSnapshot:
    device_id: int
    device_code: str
    device_version: int
    default: dict[str, Any] | None


class _DeviceRepositoryPort(Protocol):
    async def get_by_device_code(self, db: Any, device_code: str) -> Any: ...

    async def save_ecs_test_default(self, db: Any, device_code: str, default: dict[str, Any] | None) -> Any: ...


class _DeviceServicePort(Protocol):
    async def invalidate_cache(self, cache: Any, device_id: int) -> None: ...


class EcsTestDefaultService:
    def __init__(
        self,
        *,
        device_repository: _DeviceRepositoryPort = cast("_DeviceRepositoryPort", device_repository),
        device_service: _DeviceServicePort = cast("_DeviceServicePort", device_service),
    ) -> None:
        self._devices = device_repository
        self._device_service = device_service

    @staticmethod
    def _snapshot(device: Any) -> EcsTestDefaultSnapshot:
        return EcsTestDefaultSnapshot(
            device_id=device.id,
            device_code=device.device_code,
            device_version=device.version,
            default=device.ecs_test_default_json,
        )

    async def get(self, db: AsyncSession, device_code: str) -> EcsTestDefaultSnapshot | None:
        device = await self._devices.get_by_device_code(db, device_code)
        if device is None:
            return None
        return self._snapshot(device)

    async def save(
        self,
        db: AsyncSession,
        device_code: str,
        default: dict[str, Any] | None,
        *,
        cache: Any = None,
    ) -> EcsTestDefaultSnapshot | None:
        if default is not None:
            validate_ecs_test_default(
                source_device_code=device_code,
                target_device_code=default["target_device_code"],
                task_type=default["task_type"],
                params=default["params"],
            )
        device = await self._devices.save_ecs_test_default(db, device_code, default)
        if device is None:
            return None
        await self._device_service.invalidate_cache(cache, device.id)
        return self._snapshot(device)


ecs_test_default_service = EcsTestDefaultService()

__all__ = ["EcsTestDefaultService", "EcsTestDefaultSnapshot", "ecs_test_default_service"]
```

- [ ] **Step 5 — GREEN：** `uv run pytest tests/device/test_ecs_test_default_service.py -q`，全部通过。
- [ ] **Step 6 — Commit：**

```bash
git add src/app/device/domain/ecs_test_default.py src/app/device/services/ecs_test_default_service.py \
  tests/device/test_ecs_test_default_service.py
git commit -m "feat(device): ECS_TEST 默认值校验与 Service 层"
```

## Task 3：API 路由

**Files:**
- Create: `src/app/device/v1/ecs_test_default.py`
- Modify: `src/app/device/v1/__init__.py`
- Test: `tests/api/test_device_ecs_test_default_api.py`（新增）

**Interfaces:**
- Consumes: Task 2 的 `ecs_test_default_service`（通过 `request.app.state.ecs_test_default_service` 注入，模式与 `operation.py` 对 `workline_start_service` 的用法一致，方便测试用假 service 替换）。
- Produces: 路由 `GET /devices/{device_code}/ecs-test-default`、`PUT /devices/{device_code}/ecs-test-default`，权限分别为 `biz:device:detail`、`biz:device:update`（复用 Device 现有 BaseAPI 的权限命名——`perm_prefix = f"biz:device"`，`detail`/`update` 是 BaseAPI 已经在用的 action 名）。

本任务专门覆盖此前真实发生过的那类 bug（PR #276 合并后才发现 `WorkLineStartResponse` 对 ECS_TEST 返回 `plugin_version=None` 而响应模型是非空 `str`，Pydantic 在构造响应时才炸）：Step 1 的测试直接调用路由函数（不经过网络层），让响应模型的 Pydantic 校验在测试里真实跑一遍，而不是只测 service 方法的返回值。

- [ ] **Step 1 — RED：** 新建 `tests/api/test_device_ecs_test_default_api.py`：

```python
"""ECS_TEST 默认值 API 的路由声明与响应契约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Response

from src.app.device.services.ecs_test_default_service import EcsTestDefaultSnapshot
from src.app.device.v1 import ecs_test_default as api


class _FakeService:
    def __init__(self, snapshot: EcsTestDefaultSnapshot | None, *, error: Exception | None = None) -> None:
        self.snapshot = snapshot
        self.error = error
        self.save_calls: list[dict[str, Any] | None] = []

    async def get(self, _db: object, device_code: str) -> EcsTestDefaultSnapshot | None:
        return self.snapshot if self.snapshot is not None and self.snapshot.device_code == device_code else None

    async def save(
        self, _db: object, device_code: str, default: dict[str, Any] | None, *, cache: object | None = None
    ) -> EcsTestDefaultSnapshot | None:
        self.save_calls.append(default)
        if self.error is not None:
            raise self.error
        if self.snapshot is None or self.snapshot.device_code != device_code:
            return None
        return EcsTestDefaultSnapshot(
            device_id=self.snapshot.device_id,
            device_code=self.snapshot.device_code,
            device_version=self.snapshot.device_version + 1,
            default=default,
        )


def _request(service: object | None) -> object:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ecs_test_default_service=service)))


def test_routes_declare_device_detail_and_update_permissions() -> None:
    routes = {getattr(route, "path", None): route for route in api.router.routes}
    route = routes["/devices/{device_code}/ecs-test-default"]
    permissions_by_method: dict[str, list[str | None]] = {}
    for method in route.methods:
        permissions_by_method.setdefault(method, [])
    for dependency in route.dependencies:
        permission = getattr(getattr(dependency, "dependency", None), "permission_required", None)
        for method in route.methods:
            permissions_by_method[method].append(permission)
    assert "biz:device:detail" in [p for perms in permissions_by_method.values() for p in perms]
    assert "biz:device:update" in [p for perms in permissions_by_method.values() for p in perms]


@pytest.mark.asyncio
async def test_get_returns_404_body_when_device_missing() -> None:
    body = await api.get_ecs_test_default(
        device_code="MISSING",
        request=_request(_FakeService(None)),  # type: ignore[arg-type]
        response=Response(),
        db=object(),  # type: ignore[arg-type]
    )

    assert body["code"] == "3000"


@pytest.mark.asyncio
async def test_get_returns_null_default_when_none_saved() -> None:
    snapshot = EcsTestDefaultSnapshot(device_id=9, device_code="SCAN-1", device_version=3, default=None)

    body = await api.get_ecs_test_default(
        device_code="SCAN-1",
        request=_request(_FakeService(snapshot)),  # type: ignore[arg-type]
        response=Response(),
        db=object(),  # type: ignore[arg-type]
    )

    assert body["code"] == "1000"
    assert body["data"] == {"device_id": 9, "device_code": "SCAN-1", "device_version": 3, "default": None}


@pytest.mark.asyncio
async def test_put_saves_default_and_returns_advanced_version() -> None:
    snapshot = EcsTestDefaultSnapshot(device_id=9, device_code="SCAN-1", device_version=3, default=None)
    service = _FakeService(snapshot)

    body = await api.put_ecs_test_default(
        device_code="SCAN-1",
        payload=api.EcsTestDefaultUpdateRequest(
            default=api.EcsTestDefaultRule(target_device_code="TARGET-1", task_type="MOVE_FORWARD", params={})
        ),
        request=_request(service),  # type: ignore[arg-type]
        response=Response(),
        db=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
    )

    assert body["code"] == "1000"
    assert body["data"] == {
        "device_id": 9,
        "device_code": "SCAN-1",
        "device_version": 4,
        "default": {"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}},
    }
    assert service.save_calls == [{"target_device_code": "TARGET-1", "task_type": "MOVE_FORWARD", "params": {}}]


@pytest.mark.asyncio
async def test_put_with_null_default_clears_it() -> None:
    snapshot = EcsTestDefaultSnapshot(device_id=9, device_code="SCAN-1", device_version=3, default={"a": 1})
    service = _FakeService(snapshot)

    body = await api.put_ecs_test_default(
        device_code="SCAN-1",
        payload=api.EcsTestDefaultUpdateRequest(default=None),
        request=_request(service),  # type: ignore[arg-type]
        response=Response(),
        db=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
    )

    assert body["data"]["default"] is None
    assert service.save_calls == [None]


@pytest.mark.asyncio
async def test_put_returns_404_when_device_missing() -> None:
    body = await api.put_ecs_test_default(
        device_code="MISSING",
        payload=api.EcsTestDefaultUpdateRequest(default=None),
        request=_request(_FakeService(None)),  # type: ignore[arg-type]
        response=Response(),
        db=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
    )

    assert body["code"] == "3000"


@pytest.mark.asyncio
async def test_put_returns_400_when_service_rejects_payload() -> None:
    snapshot = EcsTestDefaultSnapshot(device_id=9, device_code="SCAN-1", device_version=3, default=None)
    service = _FakeService(snapshot, error=ValueError("ecs_test_rules.task_type 必须为非空字符串"))

    body = await api.put_ecs_test_default(
        device_code="SCAN-1",
        payload=api.EcsTestDefaultUpdateRequest(
            default=api.EcsTestDefaultRule(target_device_code="TARGET-1", task_type="MOVE_FORWARD", params={})
        ),
        request=_request(service),  # type: ignore[arg-type]
        response=Response(),
        db=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
    )

    assert body["code"] == "2004"


def test_update_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        api.EcsTestDefaultUpdateRequest.model_validate(
            {"default": {"target_device_code": "T", "task_type": "X", "params": {}}, "source_device_code": "SNEAKY"}
        )
```

- [ ] **Step 2 — verify RED：** `uv run pytest tests/api/test_device_ecs_test_default_api.py -q` — 应该因为 `src.app.device.v1.ecs_test_default` 不存在而报 `ModuleNotFoundError`。
- [ ] **Step 3 — 写 `src/app/device/v1/ecs_test_default.py`：**

```python
"""设备 ECS_TEST 默认值 API。"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from src.app.device.models.command import DeviceCommandParamValue
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.core.response.response_code import ClientErrorCode, ResourceErrorCode
from src.database.dependencies import AsyncSessionDep, CacheDep

router = APIRouter(tags=["设备管理"])


class EcsTestDefaultRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_device_code: str = Field(min_length=1, max_length=100)
    task_type: str = Field(min_length=1, max_length=100)
    params: dict[str, DeviceCommandParamValue] = Field(default_factory=dict)


class EcsTestDefaultUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: EcsTestDefaultRule | None = None


class EcsTestDefaultResponse(BaseModel):
    device_id: int
    device_code: str
    device_version: int
    default: EcsTestDefaultRule | None


def _not_found(device_code: str) -> dict[str, Any]:
    return response_builder.fail(code=ResourceErrorCode.NOT_FOUND, message=f"设备不存在: {device_code}")


@router.get(
    "/devices/{device_code}/ecs-test-default",
    summary="[biz:device:detail] 读取设备的 ECS_TEST 默认值",
    response_model=ResponseSchemaModel[EcsTestDefaultResponse],
    dependencies=[Depends(RequirePermission("biz:device:detail"))],
)
async def get_ecs_test_default(
    db: AsyncSessionDep,
    request: Request,
    response: Response,
    device_code: str = Path(...),
) -> dict[str, Any]:
    service = getattr(request.app.state, "ecs_test_default_service", None)
    if service is None:
        return response_builder.fail(code=ClientErrorCode.BAD_REQUEST, message="ECS_TEST 默认值服务不可用")
    snapshot = await service.get(db, device_code)
    if snapshot is None:
        response.status_code = ResourceErrorCode.NOT_FOUND.http_status
        return _not_found(device_code)
    data = EcsTestDefaultResponse(
        device_id=snapshot.device_id,
        device_code=snapshot.device_code,
        device_version=snapshot.device_version,
        default=snapshot.default,
    )
    return response_builder.success(data=data.model_dump(mode="json"))


@router.put(
    "/devices/{device_code}/ecs-test-default",
    summary="[biz:device:update] 保存或清除设备的 ECS_TEST 默认值",
    response_model=ResponseSchemaModel[EcsTestDefaultResponse],
    dependencies=[Depends(RequirePermission("biz:device:update"))],
)
async def put_ecs_test_default(
    db: AsyncSessionDep,
    cache: CacheDep,
    request: Request,
    response: Response,
    payload: EcsTestDefaultUpdateRequest,
    device_code: str = Path(...),
) -> dict[str, Any]:
    service = getattr(request.app.state, "ecs_test_default_service", None)
    if service is None:
        response.status_code = ClientErrorCode.BAD_REQUEST.http_status
        return response_builder.fail(code=ClientErrorCode.BAD_REQUEST, message="ECS_TEST 默认值服务不可用")
    default = payload.default.model_dump() if payload.default is not None else None
    try:
        snapshot = await service.save(db, device_code, default, cache=cache)
    except ValueError as exc:
        response.status_code = ClientErrorCode.VALIDATION_ERROR.http_status
        return response_builder.fail(code=ClientErrorCode.VALIDATION_ERROR, message=str(exc))
    if snapshot is None:
        response.status_code = ResourceErrorCode.NOT_FOUND.http_status
        return _not_found(device_code)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    data = EcsTestDefaultResponse(
        device_id=snapshot.device_id,
        device_code=snapshot.device_code,
        device_version=snapshot.device_version,
        default=snapshot.default,
    )
    return response_builder.success(data=data.model_dump(mode="json"))


__all__ = [
    "EcsTestDefaultRule",
    "EcsTestDefaultUpdateRequest",
    "EcsTestDefaultResponse",
    "router",
]
```

（`cast` import 暂时用不到——如果 ruff 报未使用 import，删掉这一行。）

- [ ] **Step 4 — 挂载路由：** 修改 `src/app/device/v1/__init__.py`：

```python
"""Device V1 API 导出。"""

from fastapi import APIRouter

from .device import router as device_router
from .ecs_callback import router as ecs_callback_router
from .ecs_test_default import router as ecs_test_default_router

router = APIRouter()
router.include_router(device_router)
router.include_router(ecs_callback_router, prefix="/callback")
router.include_router(ecs_test_default_router)

__all__ = ["router"]
```

- [ ] **Step 5 — 挂载 service 到 app.state：** `ecs_test_default_service` 是无状态模块级单例（只依赖同样是单例的 `device_repository`/`device_service`），不需要经过 `build_deployment_runtime` 的装配，也不需要 `DeploymentRuntime` 携带。直接在 `src/register.py` 顶部加一个导入，并对称地在现有 `workline_start_service` 出现的三处各加一行：

在文件顶部的 import 区（`from .core.conf import settings` 那一行附近）加：

```python
from src.app.device.services.ecs_test_default_service import ecs_test_default_service
```

`register.py:51`（`register_init` 函数体最前面，init 开始前的 fail-closed 重置块）：

```python
        _app.state.deployment_runtime = None
        _app.state.workline_start_service = None
        _app.state.ecs_test_default_service = None
        _app.state.workline_configuration_service = None
```

`register.py:120`（`deployment_runtime = build_deployment_runtime(...)` 成功之后的正常赋值块）：

```python
        _app.state.workline_start_service = deployment_runtime.workline_start_service
        _app.state.ecs_test_default_service = ecs_test_default_service
        _app.state.workline_configuration_service = deployment_runtime.workline_configuration_service
```

`register.py:167`（`finally` 块，进程关闭或 init 失败时的重置）：

```python
        _app.state.deployment_runtime = None
        _app.state.workline_start_service = None
        _app.state.ecs_test_default_service = None
        _app.state.workline_configuration_service = None
```

这一步没有独立测试，由 Task 4 的最终回归覆盖（应用启动不报错即可，`register.py` 目前没有专门测试三处赋值是否存在）。
- [ ] **Step 6 — GREEN：** `uv run pytest tests/api/test_device_ecs_test_default_api.py -q`，全部通过。
- [ ] **Step 7 — Commit：**

```bash
git add src/app/device/v1/ecs_test_default.py src/app/device/v1/__init__.py src/register.py \
  tests/api/test_device_ecs_test_default_api.py
git commit -m "feat(device): ECS_TEST 默认值 API 路由"
```

## Task 4：HEAVY mapping 与最终回归

**Files:**
- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `tests/scripts/test_select_heavy_tests.py`（如果新迁移落在 golden 列表覆盖范围内，需要同步更新，参照 PR #276 Task 4 处理同一个坑的方式）

**Interfaces:** 无新增代码接口，只有测试基础设施。

- [ ] **Step 1：** 运行 `uv run python scripts/select_heavy_tests.py --base develop`（或当前分支的实际 base），把每一条 `候选路径未配置 mapping/NONE` 报错对应的文件加一条 `[[mapping]]`：
  - `src/app/device/domain/ecs_test_default.py`、`src/app/device/services/ecs_test_default_service.py`、`src/app/device/v1/ecs_test_default.py`：`heavy_tests = ["tests/integration/device_command/test_device_command_constraints.py"]`（跟 `ecs_test_contracts.py` 用同一条兜底 HEAVY 测试，因为都是 DeviceCommand 相关基础设施的一部分）。
  - 新迁移文件：`heavy_tests = ["tests/integration/test_initial_schema_baseline_postgresql.py", "tests/integration/device_command/test_ecs_test_default_postgresql.py"]`，追加在 `migrations/versions/` mapping 块的**末尾**（不要插在中间——PR #276 就因为插在中间导致跟 `test_select_heavy_tests.py` 里那条 golden 顺序测试冲突过一次）。
  - 重跑 `select_heavy_tests.py` 直到不再报 `fail closed`。
- [ ] **Step 2：** 如果 `tests/scripts/test_select_heavy_tests.py` 里的 `test_initial_schema_revision_mapping_is_exact_after_tombstone_cleanup`（或同类 golden 列表测试）因为新迁移条目而失败，把新迁移的 `source_glob` 追加到该测试期望列表的**末尾**（顺序必须跟 `heavy-test-impact.toml` 里实际的文件顺序完全一致）。
- [ ] **Step 3：** 全量回归：

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
```

- [ ] **Step 4：** 真实 Postgres 集成回归（复用 Task 1 已经验证过的测试文件，加上迁移链）：

```bash
uv run alembic upgrade head
INTEGRATION_DATABASE_URL=<...> uv run pytest tests/integration/device_command/test_ecs_test_default_postgresql.py -q
```

- [ ] **Step 5 — Commit：**

```bash
git add docs/architecture/heavy-test-impact.toml tests/scripts/test_select_heavy_tests.py
git commit -m "docs(device): ECS_TEST 默认值的 HEAVY selector mapping"
```

## 执行选择

四个任务顺序依赖（Task 2 依赖 Task 1 的 Repository 方法，Task 3 依赖 Task 2 的 Service，Task 4 收尾），建议按顺序执行，不要并行拆给独立 agent。
