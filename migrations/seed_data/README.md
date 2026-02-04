# 初始数据 (Seed Data)

本目录包含数据库初始化数据 SQL 文件，由 Alembic 迁移脚本调用。

## 📁 文件说明

### initial_data.sql
**用途**: 初始化系统基础数据
**内容**:
- PostgreSQL Schemas (wes_sys, wes_biz)
- 权限 (Permissions) - API 和菜单权限
- 角色 (Roles) - 超级管理员、管理员、运营人员、财务人员、普通用户
- 用户 (Users) - 默认测试账号
- 角色权限关联 (Role-Permissions)
- 用户角色关联 (User-Roles)

**调用**: 由 `20260130_1549_2e783457488e_initial_migration.py` 在 upgrade() 时执行

**默认测试账号**:
```
admin / admin123     (超级管理员)
manager / admin123    (管理员)
operator / admin123   (运营人员)
finance / admin123    (财务人员)
user1 / admin123      (普通用户)
user2 / admin123      (普通用户)
```

⚠️ **生产环境请立即修改默认密码！**

## 🎯 设计原则

遵循以下原则：

- **DRY (Don't Repeat Yourself)**: 数据只在 SQL 文件中定义一次
- **KISS (Keep It Simple, Stupid)**: 使用纯 SQL，简单易懂
- **SOLID**: 单一职责 - SQL 负责数据定义，迁移负责执行逻辑
- **YAGNI (You Aren't Gonna Need It)**: 不过度设计，直接使用 SQL 文件

## 📋 使用方式

### 通过 Alembic 迁移（推荐）

```bash
# 首次部署：创建 schemas + 表 + 初始数据
alembic upgrade head
```

### 手动执行（仅用于开发/调试）

```bash
# 在数据库容器内执行
docker exec -i wes_postgres_prod psql -U wesuser -d wesdb < migrations/seed_data/initial_data.sql
```

## 🔄 修改初始数据

如果需要修改初始数据：

1. **编辑 SQL 文件**: `migrations/seed_data/initial_data.sql`
2. **创建新迁移**: `./scripts/generate_migration.sh "update_initial_data"`
3. **在新迁移中更新数据**:
   ```python
   def upgrade() -> None:
       # 更新初始数据
       op.execute("UPDATE wes_sys.users SET ...")
   ```

⚠️ **不要直接修改已执行的迁移文件**，应该创建新的迁移来更新数据。

## 📊 数据统计

执行 initial_data.sql 后，将创建：

| 项目 | 数量 |
|------|------|
| Schemas | 2 (wes_sys, wes_biz) |
| 权限 | ~30 条 |
| 角色 | 5 个 |
| 用户 | 6 个 |
| 角色权限关联 | ~50 条 |
| 用户角色关联 | 6 条 |

## 🔒 安全注意事项

1. **密码哈希**: SQL 文件中的密码已使用 Argon2 哈希
2. **生产环境**: 必须修改所有默认密码
3. **敏感数据**: 不要在 SQL 文件中存储生产环境的真实数据
4. **版本控制**: SQL 文件已纳入 Git，确保不包含敏感信息

---

**更新时间**: 2026-02-04
