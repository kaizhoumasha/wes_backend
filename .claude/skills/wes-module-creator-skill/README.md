# WES Module Creator Skill

WES Backend 功能模块创建技能，提供标准化的模块创建工作流和自动化代码生成。

## 功能特性

- ✅ 基于 Jinja2 模板系统的灵活代码生成
- ✅ 基于 Jinja2 模板系统的灵活代码生成
- ✅ 支持 YAML 配置文件定义模块结构
- ✅ 自动更新 migrations/env.py 和 src/register.py
- ✅ 支持字段定义（枚举、外键、索引等）
- ✅ 支持关系定义（一对一、一对多、多对多）
- ✅ 支持平面结构和树形结构两种模式
- ✅ 智能 Mixin 组合选择
- ✅ 完整的 CRUD 代码生成
- ✅ 自动生成单元测试
- ✅ 交互式问答式模块创建
- ✅ 遵循 RUFF 和 CLAUDE.md 规范
- ✅ 包含详细的参考文档和最佳实践

## 快速开始

### 前置要求

```bash
# 安装依赖
cd .claude/skills/wes-module-creator-skill/scripts
pip install -r requirements.txt
```

### 方式 1：使用基于模板的生成器 V2（推荐）

```bash
# 基础用法
python scripts/module_generator_v2.py --name warehouse

# 使用 YAML 配置文件（推荐）
python scripts/module_generator_v2.py --config examples/device_config.yaml

# 树形结构
python scripts/module_generator_v2.py --name category --tree

# 指定 Mixin
python scripts/module_generator_v2.py --name product \
  --mixins DataTableMixin,EnterpriseMixin,SoftDeleteMixin

# 生成单元测试
python scripts/module_generator_v2.py --name warehouse --tests
```

**V2 生成器特性**：
- ✅ 自动更新 migrations/env.py（添加模型导入）
- ✅ 自动更新 src/register.py（注册路由）
- ✅ 支持字段定义（枚举、外键、索引等）
- ✅ 支持关系定义（一对一、一对多、多对多）
- ✅ YAML 配置文件支持
- ✅ 自动生成单元测试

### 方式 2：使用交互式生成器（Q&A 模式）

```bash
# 启动交互式问答
python scripts/interactive_generator.py
```

**交互式生成器特性**：
- ✅ 5 阶段问答式工作流（基本信息、结构、Mixin、字段、关系）
- ✅ 代码预览功能
- ✅ 自动保存配置文件
- ✅ 适合不熟悉命令行的用户

### 方式 2：使用原始生成器 V1

```bash
# 创建平面结构模块
python scripts/generate_module.py --name warehouse --flat

# 创建树形结构模块
python scripts/generate_module.py --name category --tree

# 指定 Mixin
python scripts/generate_module.py \
  --name product \
  --mixins DataTableMixin,EnterpriseMixin,SoftDeleteMixin
```

### 生成的文件结构

```
src/app/biz/{module}/
├── models/
│   ├── __init__.py
│   └── {module}.py          # 数据模型
├── repositories/
│   ├── __init__.py
│   └── {module}_repository.py  # Repository
├── services/
│   ├── __init__.py
│   └── {module}_service.py     # Service
└── v1/
    ├── __init__.py
    └── {module}.py          # API 路由
```

## 文档

### 核心文档
- **SKILL.md** - 技能使用指南和常见陷阱
- **USAGE.md** - 详细使用说明

### 参考文档
- **references/best-practices.md** - 最佳实践
- **references/mixin-guide.md** - Mixin 选择指南
- **references/complete-example.md** - 完整示例（含常见错误）
- **references/hook-system.md** - Hook 系统详解
- **references/status-validation.md** - 状态验证系统
- **references/relationship-guide.md** - SQLModel 关系定义指南
- **references/template-system-guide.md** - 模板系统使用指南（新增）
- **references/troubleshooting.md** - 故障排查指南
- **references/checklist.md** - 模块创建验证清单

### 示例文件
- **examples/device_config.yaml** - YAML 配置文件示例（新增）

## 工作流程

1. **定义数据模型** - 选择合适的 Mixin，考虑是否需要树形结构
2. **定义 Repository** - 继承 BaseRepository 或 TreeRepository
3. **定义 Service** - 继承 BaseService 或 TreeServiceMixin
4. **定义 API** - 继承 BaseAPI 或 TreeAPI

## 后续步骤

生成模块后（V2 生成器已自动完成路由注册和模型导入）：

1. ✨ 路由已在 `src/register.py` 中注册（自动完成）
2. ✨ 模型已在 `migrations/env.py` 中导入（自动完成）
3. 📝 运行代码检查：`ruff format . && ruff check .`
4. 🗄️  生成数据库迁移：`./scripts/generate_migration.sh 'Add {module} module'`
5. ⬆️  运行迁移：`./scripts/migrate.sh upgrade`
6. 🧪 运行测试：`pytest tests/test_{module}.py`（如果使用了 `--tests` 参数）

## 技能打包

```bash
# 打包技能（需要 skill-creator 的 package_skill.py）
python scripts/package_skill.py wes-module-creator-skill
```

## 许可证

MIT
