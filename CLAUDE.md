# CLAUDE.md

P9 WES Backend 快速开发框架指南 - 基于 FastAPI + SQLModel + SQLAlchemy 2.0

## 项目概述

专为 WMS/WES 系统设计的快速开发框架，采用**分层架构**和**零代码开发模式**。

**核心特性**：
- **零代码 CRUD**：继承 BaseAPI 自动生成 REST API
- **ModelFactory**：自动生成 Create/Update Schema
- **Hook 系统**：Repository 层业务逻辑扩展
- **Mixin 组合**：复用模型字段和行为
- **RBAC 权限**：基于角色的访问控制
- **TimescaleDB**：时序数据存储

**基础设施**：
- Postgres (TimescaleDB) `wes_postgres`
- Redis `wes_redis`

## 开发命令

```bash
# 环境管理
docker-compose up -d          # 启动基础设施
uv sync                       # 安装依赖
./scripts/migrate.sh upgrade  # 数据库迁移
uvicorn main:app --reload     # 开发服务器

# 代码质量
ruff format . && ruff check . # 格式化和检查
pytest --cov=src              # 测试和覆盖率
```

---

## 🚨 关键规则（CRITICAL）

### 分层架构

```
API 层 → Service 层 → Repository 层 → 数据库
```

**严格禁止**：
- ❌ API 层直接访问数据库 (`db.execute`, `select()`)
- ❌ API 层直接调用 Repository
- ❌ 任何跨层直接调用

**检测命令**：
```bash
grep -r "from sqlalchemy import select" src/app/*/v1/
grep -r "db.execute(" src/app/*/v1/
```

### Mixin 继承规范

**⚠️ EnterpriseMixin 已包含 AuditMixin + OptimisticLockMixin**

```python
# ❌ 错误：重复继承
class User(UserBase, AuditMixin, EnterpriseMixin, SoftDeleteMixin, table=True)

# ✅ 正确
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True)
```

### 时区使用

| 场景 | 方法 | 返回类型 |
|------|------|----------|
| 数据库存储 | `timezone.now_for_db()` | naive UTC |
| API 响应 | `timezone.now_utc().isoformat()` | aware ISO |
| 时间戳计算 | `timezone.now_utc().timestamp()` | Unix 秒 |

**⚠️ 禁止对 naive datetime 调用 `.timestamp()`**

### 模块导出

新 Service 必须在 `__init__.py` 中导出：

```python
from .xxx_service import XxxService, xxx_service

__all__ = ["XxxService", "xxx_service"]
```

---

## 设计原则

| 原则 | 应用 |
|------|------|
| **DRY** | Mixin 复用字段，ModelFactory 生成 Schema，基类复用 CRUD |
| **KISS** | 优先基类默认实现，不过度抽象 |
| **SOLID** | Repository/Service/API 单一职责，Hook/Mixin 扩展功能 |
| **YAGNI** | 只实现当前需求，不预设计 |

---

## 模型定义模式

```python
# 1. 基础字段（用于 Schema 复用）
class UserBase(BaseMixin):
    username: str
    email: str

# 2. 数据库表模型（Base + Mixins + 表特有字段）
class User(UserBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "users"
    hashed_password: str

# 3. Schema（ModelFactory 自动生成）
class UserCreate(ModelFactory(UserBase).for_create()):
    password: str

class UserUpdate(ModelFactory(UserBase).for_update()):
    pass

# 4. Repository/Service/API（零代码）
class UserRepository(BaseRepository[User]):
    pass

class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository(), enable_cache=True)

user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=UserService(),
    create_schema=UserCreate,
    update_schema=UserUpdate,
    response_schema=UserResponse,
    prefix="/users",
)
```

---

## 文档索引

| 文档 | 位置 | 内容 |
|------|------|------|
| 项目文件索引 | [docs/file_index.md](docs/file_index.md) | 代码结构、目录说明、快速查找、响应码 |
| 核心架构 | [.claude/context/architecture.md](.claude/context/architecture.md) | 分层架构、Hook/Mixin 系统、状态验证、JWT/RBAC |
| 开发规则 | [.claude/context/rules.md](.claude/context/rules.md) | 分层架构规则、Service 调用、模块导出、时区规则 |
| 常见任务 | [.claude/context/howto.md](.claude/context/howto.md) | 创建模块、自定义逻辑、状态验证、树形结构 |
| 故障排查 | [.claude/context/troubleshooting.md](.claude/context/troubleshooting.md) | 缓存问题、N+1 查询、架构违规、ImportError |

---

## 关键文件

**核心框架**：
- `src/database/base_repository.py` - Repository 基类
- `src/core/base_service.py` - Service 基类
- `src/core/base_api.py` - API 基类
- `src/database/model_factory.py` - Schema 工厂

**Mixin 系统**：
- `src/core/mixins/__init__.py` - 所有可用 Mixin

**认证权限**：
- `src/core/security.py` - JWT 认证
- `src/core/rbac.py` - RBAC 权限

---

## 文档同步规则

每次更新功能后，**必须**同步更新 `docs/file_index.md`。

```bash
# 验证文档同步
serena list_dir . --recursive --skip-ignored
# 对比 docs/file_index.md
```