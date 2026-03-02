# WES Backend 模块创建检查清单

使用本检查清单确保新创建的模块完整且符合规范。

## 📋 模块创建前

### 需求分析
- [ ] 明确模块的业务功能和职责
- [ ] 确定是否需要树形结构（父子关系）
- [ ] 确定需要的 Mixin 组合
- [ ] 确定是否需要软删除功能
- [ ] 确定是否需要审计日志
- [ ] 列出所有业务字段和验证规则

### 架构设计
- [ ] 确定模块名称（单数形式，如 `device`）
- [ ] 确定表名（复数形式，如 `devices`）
- [ ] 确定 Schema 类型（`wes_sys` 或 `wes_biz`）
- [ ] 确定与其他模块的关系（外键、关联）
- [ ] 确定权限码前缀（如 `biz:device:*`）

---

## 📝 代码生成检查

### Models 层
- [ ] 创建了 `{module}Base` 类（只包含业务字段）
- [ ] 创建了 `{Module}` 表模型（Base + Mixins + 表特有字段）
- [ ] 使用 `ModelFactory` 生成 Create/Update Schema
- [ ] 创建了 `{Module}Response` Schema
- [ ] 正确设置了 `__tablename__` 和 `__schema__`
- [ ] 字段包含适当的验证（`Field(max_length=...)`）
- [ ] 如果有关系，Relationship 类型注解**不使用** Union 类型（`| None`）
- [ ] 如果有枚举，定义了对应的 Enum 类

**关键检查**：
```python
# ✅ 正确的 Relationship 定义
work_line: "WorkLine" = Relationship(...)

# ❌ 错误的 Relationship 定义
work_line: "WorkLine | None" = Relationship(...)
```

### Repository 层
- [ ] 创建了 `{Module}Repository` 类
- [ ] 继承了正确的基类（`BaseRepository` 或 `TreeRepository`）
- [ ] **包含 `__init__` 方法**并调用 `super().__init__(Model)`
- [ ] 如果需要，添加了自定义查询方法（如 `get_by_code`）
- [ ] 创建了单例实例（`{module}_repository`）

**关键检查**：
```python
# ✅ 正确的 Repository 定义
class DeviceRepository(BaseRepository[Device]):
    def __init__(self) -> None:
        """初始化设备仓库"""
        super().__init__(Device)

device_repository = DeviceRepository()
```

### Service 层
- [ ] 创建了 `{Module}Service` 类
- [ ] 继承了正确的基类（`BaseService` 或 `TreeServiceMixin`）
- [ ] 在 `__init__` 中配置了缓存（如果需要）
- [ ] 如果需要，添加了自定义业务方法
- [ ] 如果需要，添加了 Hook（状态验证、审计等）
- [ ] 创建了单例实例（`{module}_service`）

### API 层
- [ ] 使用 `BaseAPI` 或 `TreeAPI` 创建路由
- [ ] 正确配置了 `module_name`、`prefix`、`tags`
- [ ] 传递了正确的 Schema（Create/Update/Response）
- [ ] 如果需要，添加了自定义路由（`custom_routes`）
- [ ] 导出了 `router` 对象

### __init__.py 文件
- [ ] `models/__init__.py` 导出了所有模型和 Schema
- [ ] `repositories/__init__.py` 导出了 Repository 类和单例
- [ ] `services/__init__.py` 导出了 Service 类和单例
- [ ] `v1/__init__.py` 导出了 `router`
- [ ] 模块根目录的 `__init__.py` 导出了 `router_v1`

---

## 🔧 集成检查

### 数据库迁移
- [ ] 在 `migrations/env.py` 中添加了模型导入
- [ ] 导入语句包含 `# noqa: F401` 注释
- [ ] 生成了迁移脚本：`./scripts/generate_migration.sh "Add {module} module"`
- [ ] 检查了迁移脚本内容（表结构、索引、外键）
- [ ] 执行了迁移：`./scripts/migrate.sh upgrade`
- [ ] 验证了数据库表已创建

**关键检查**：
```python
# migrations/env.py
from src.app.device.models import Device  # noqa: F401
from src.app.workline.models import WorkLine  # noqa: F401
```

### 路由注册
- [ ] 在 `src/register.py` 中导入了路由
- [ ] 在 `register_routers` 函数中注册了路由
- [ ] 重启了开发服务器
- [ ] 访问 Swagger UI 验证路由已注册（`http://localhost:8001/docs`）

**关键检查**：
```python
# src/register.py
def register_routers(app: FastAPI) -> None:
    from src.app.device import router_v1 as device_router
    app.include_router(device_router, prefix=settings.API_PATH)
```

---

## ✅ 代码质量检查

### 格式和 Lint
- [ ] 运行 `ruff format .` 格式化代码
- [ ] 运行 `ruff check .` 检查代码质量
- [ ] 修复所有 lint 错误和警告
- [ ] 确保没有未使用的导入

### 类型检查
- [ ] 所有函数参数都有类型注解
- [ ] 所有函数返回值都有类型注解
- [ ] 使用了正确的泛型类型（如 `BaseRepository[Device]`）

### 文档
- [ ] 所有类都有中文文档字符串
- [ ] 关键方法都有中文文档字符串
- [ ] 字段都有 `description` 参数

---

## 🧪 功能测试

### 基础 CRUD 测试
- [ ] 测试创建记录（POST `/api/{module}s`）
- [ ] 测试查询单个记录（GET `/api/{module}s/{id}`）
- [ ] 测试查询列表（POST `/api/{module}s/query`）
- [ ] 测试更新记录（PUT `/api/{module}s/{id}`）
  - [ ] 更新数据包含 `version` 字段
- [ ] 测试删除记录（DELETE `/api/{module}s/{id}`）

### 软删除测试（如果使用 SoftDeleteMixin）
- [ ] 测试软删除（DELETE `/api/{module}s/{id}`）
- [ ] 测试查询回收站（GET `/api/{module}s/trash`）
- [ ] 测试恢复记录（POST `/api/{module}s/{id}/restore`）
- [ ] 测试永久删除（DELETE `/api/{module}s/{id}?permanent=true`）

### 关系测试（如果有外键关系）
- [ ] 测试创建带关系的记录
- [ ] 测试查询时自动加载关系
- [ ] 测试级联删除或设置为 NULL

### 权限测试
- [ ] 测试未授权访问（应返回 403）
- [ ] 测试已授权访问（应返回 200）
- [ ] 验证权限码格式正确（`{module}:{resource}:{action}`）

### 验证测试
- [ ] 测试字段长度验证
- [ ] 测试必填字段验证
- [ ] 测试唯一约束验证
- [ ] 测试外键约束验证

---

## 📊 性能检查

### 查询优化
- [ ] 使用 Schema 驱动的自动关系加载（避免 N+1 查询）
- [ ] 配置了适当的索引（业务主键、外键）
- [ ] 启用了缓存（如果适用）

### 缓存配置
- [ ] Service 层启用了缓存（`enable_cache=True`）
- [ ] 配置了合适的缓存前缀（`cache_prefix`）
- [ ] 配置了合适的缓存过期时间（`cache_expire`）

---

## 📚 文档更新

### 项目文档
- [ ] 更新了 `docs/file_index.md`（如果有）
- [ ] 更新了 API 文档（Swagger 自动生成）
- [ ] 如果有特殊业务逻辑，添加了说明文档

### 代码注释
- [ ] 复杂的业务逻辑有注释说明
- [ ] Hook 的用途有注释说明
- [ ] 特殊的验证规则有注释说明

---

## 🚀 部署前检查

### Git 提交
- [ ] 代码已提交到 Git
- [ ] 提交信息清晰描述了变更
- [ ] 没有提交敏感信息（密码、密钥等）

### 数据库
- [ ] 迁移脚本已提交
- [ ] 迁移脚本在测试环境验证通过
- [ ] 准备了回滚方案（downgrade 函数）

### 测试
- [ ] 所有测试通过
- [ ] 手动测试了关键功能
- [ ] 验证了错误处理

---

## ⚠️ 常见陷阱检查

### 高优先级（必须检查）
- [ ] ✅ Repository 包含 `__init__` 方法
- [ ] ✅ Relationship 类型注解不使用 `| None`
- [ ] ✅ migrations/env.py 已添加模型导入
- [ ] ✅ src/register.py 已注册路由
- [ ] ✅ 更新操作包含 `version` 字段

### 中优先级（建议检查）
- [ ] Service 方法调用时传递了 `cache` 参数
- [ ] 外键字段可以为 NULL（`work_line_id: int | None`）
- [ ] 唯一约束字段有部分唯一索引（软删除场景）
- [ ] 错误信息友好且中文化

---

## 📝 完成标记

完成所有检查后，在此签名：

- **模块名称**：_________________
- **创建日期**：_________________
- **创建人**：___________________
- **审核人**：___________________
- **状态**：□ 开发中  □ 测试中  □ 已完成

---

## 💡 提示

- 使用本检查清单作为模板，为每个新模块创建一份副本
- 在代码审查时使用本检查清单
- 定期更新检查清单，添加新的最佳实践
- 将常见问题添加到 `troubleshooting.md`
