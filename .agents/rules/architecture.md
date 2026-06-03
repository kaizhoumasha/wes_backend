# Architecture Rules

本文件是 AGY / Antigravity workspace rule，内容来自 `AGENTS.md`。`AGENTS.md` 是项目规则主真源；如有冲突，以 `AGENTS.md` 为准。

## Layered Architecture

必须保持：

```text
API Layer -> Service Layer -> Repository Layer -> Database
```

严格禁止：

- API 层直接访问数据库，例如 `db.execute`、`select()`。
- API 层直接调用 Repository。
- 跨层直接调用。

检测命令：

```bash
rg -n "from sqlalchemy import select|db\\.execute\\(" src/app/*/v1/
```

## Mixins

`EnterpriseMixin` 已包含审计和乐观锁能力，不要重复继承 `AuditMixin` 或 `OptimisticLockMixin`。

推荐组合：

```python
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    pass
```

## Schema

- 继承 `EnterpriseMixin` 的模型，Update Schema 使用 `for_optimistic_update()`。
- 只继承 `DataTableMixin` + `SoftDeleteMixin` 且无乐观锁的模型，Update Schema 使用 `for_update()`。

## Timezone

| Scenario | Method |
| --- | --- |
| 数据库存储 | `timezone.now_for_db()` |
| API 响应 | `timezone.now_utc().isoformat()` |
| 时间戳计算 | `timezone.now_utc().timestamp()` |

禁止对 naive datetime 调用 `.timestamp()`。

## Alembic

新增迁移必须通过 Alembic revision generator 创建：

```bash
uv run alembic revision -m "<message>"
```

不要手写模板化 `revision` ID。先生成，再编辑迁移内容。

## Module Export

新增 Service 必须在对应 `__init__.py` 导出：

```python
from .xxx_service import XxxService, xxx_service

__all__ = ["XxxService", "xxx_service"]
```
