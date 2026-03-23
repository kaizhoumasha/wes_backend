# 数据库迁移指南

本项目使用 Alembic 进行数据库迁移管理。

## 快速开始

### 1. 应用现有迁移

首次部署或更新代码后，运行以下命令应用所有迁移：

```bash
./scripts/migrate.sh upgrade
```

### 2. 创建新迁移

修改模型后，创建新的迁移文件：

```bash
./scripts/migrate.sh create "描述你的变更"
```

例如：
```bash
./scripts/migrate.sh create "add email verification field to users"
```

### 3. 查看迁移状态

```bash
./scripts/migrate.sh current   # 查看当前版本
./scripts/migrate.sh history   # 查看迁移历史
./scripts/migrate.sh check     # 检查是否有待应用的迁移
```

## 常用命令

### 升级数据库

```bash
./scripts/migrate.sh upgrade          # 升级到最新版本
./scripts/migrate.sh upgrade +1       # 升级一个版本
./scripts/migrate.sh upgrade <revision>  # 升级到指定版本
```

### 回滚数据库

```bash
./scripts/migrate.sh downgrade -1     # 回滚一个版本
./scripts/migrate.sh downgrade <revision>  # 回滚到指定版本
./scripts/migrate.sh downgrade base   # 回滚所有迁移（清空数据库）
```

### 查看迁移信息

```bash
./scripts/migrate.sh show <revision>  # 查看指定迁移的详细信息
./scripts/migrate.sh heads            # 查看当前的 head 版本
```

## 直接使用 Alembic

如果需要更高级的功能，可以直接使用 Alembic 命令：

```bash
alembic revision --autogenerate -m "message"  # 创建迁移
alembic upgrade head                          # 升级到最新
alembic downgrade -1                          # 回滚一个版本
alembic current                               # 查看当前版本
alembic history                               # 查看历史
```

## 工作流程

### 开发环境

1. **修改模型**：在 `src/app/*/models/` 中修改 SQLModel 模型
2. **创建迁移**：`./scripts/migrate.sh create "描述变更"`
3. **检查迁移文件**：查看 `migrations/versions/` 中生成的迁移文件
4. **应用迁移**：`./scripts/migrate.sh upgrade`
5. **测试**：确保应用正常运行

### 生产环境

1. **备份数据库**：在应用迁移前务必备份
2. **检查待应用的迁移**：`./scripts/migrate.sh check`
3. **应用迁移**：`./scripts/migrate.sh upgrade`
4. **验证**：确认应用正常运行

## 注意事项

### 1. 模型导入

所有需要迁移的模型必须在 `alembic/env.py` 中导入：

```python
from src.app.admin.models import User, Role, Permission
from src.app.demo.models.demo_product import DemoProduct
```

如果添加了新的模型文件，需要在 `env.py` 中添加相应的导入。

### 2. 迁移文件命名

迁移文件自动使用时间戳命名：`YYYYMMDD_HHMM_<revision>_<message>.py`

### 3. 数据迁移

Alembic 可以自动检测结构变更，但数据迁移需要手动编写：

```python
def upgrade() -> None:
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), nullable=True))
    
    op.execute("UPDATE users SET email_verified = false WHERE email_verified IS NULL")
    
    op.alter_column('users', 'email_verified', nullable=False)
```

### 4. 回滚策略

- 开发环境：可以自由回滚和重新生成迁移
- 生产环境：避免回滚，优先创建新的迁移来修复问题

### 5. 多分支开发

如果多个分支同时创建了迁移，可能会出现分叉：

```bash
alembic merge <rev1> <rev2> -m "merge branches"
```

## 配置说明

### alembic.ini

主要配置项：

- `script_location`: 迁移脚本位置（`migrations/`）
- `file_template`: 迁移文件命名格式（包含时间戳）
- `timezone`: 时区设置（`Asia/Shanghai`）
- `hooks`: 自动运行 ruff 格式化

### alembic/env.py

- 从 `src.core.conf.settings` 读取数据库 URL
- 导入所有 SQLModel 模型
- 配置异步数据库支持
- 支持 PostgreSQL 特性（类型比较、默认值比较）

## 故障排除

### 问题：迁移检测不到模型变更

**解决方案**：
1. 确认模型已在 `alembic/env.py` 中导入
2. 确认模型类使用了 `table=True` 参数
3. 检查 `target_metadata` 是否正确设置

### 问题：迁移失败

**解决方案**：
1. 检查数据库连接配置（`.env` 文件）
2. 查看错误信息，可能是数据约束冲突
3. 如果是开发环境，可以回滚后重新生成迁移

### 问题：多个 head 版本

**解决方案**：
```bash
alembic merge <rev1> <rev2> -m "merge heads"
./scripts/migrate.sh upgrade
```

## 最佳实践

1. **频繁提交**：每次模型变更都创建迁移，不要积累多个变更
2. **描述清晰**：迁移消息要清楚描述变更内容
3. **测试迁移**：在开发环境测试升级和回滚
4. **代码审查**：迁移文件也需要代码审查
5. **备份数据**：生产环境应用迁移前务必备份
6. **版本控制**：迁移文件必须提交到版本控制系统

## 参考资料

- [Alembic 官方文档](https://alembic.sqlalchemy.org/)
- [SQLModel 文档](https://sqlmodel.tiangolo.com/)
- [SQLAlchemy 文档](https://docs.sqlalchemy.org/)
