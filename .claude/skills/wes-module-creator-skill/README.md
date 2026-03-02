# WES Module Creator Skill

WES Backend 功能模块创建技能，提供标准化的模块创建工作流和自动化代码生成。

## 功能特性

- ✅ 自动生成符合项目架构规范的代码
- ✅ 支持平面结构和树形结构两种模式
- ✅ 智能 Mixin 组合选择
- ✅ 完整的 CRUD 代码生成
- ✅ 遵循 RUFF 和 CLAUDE.md 规范
- ✅ 包含详细的参考文档和最佳实践

## 快速开始

### 使用自动化脚本

```bash
# 创建平面结构模块
python wes-module-creator-skill/scripts/generate_module.py --name warehouse --flat

# 创建树形结构模块
python wes-module-creator-skill/scripts/generate_module.py --name category --tree

# 指定 Mixin 组合
python wes-module-creator-skill/scripts/generate_module.py \
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
- **references/relationship-guide.md** - SQLModel 关系定义指南（新增）
- **references/troubleshooting.md** - 故障排查指南（新增）
- **references/checklist.md** - 模块创建验证清单（新增）

## 工作流程

1. **定义数据模型** - 选择合适的 Mixin，考虑是否需要树形结构
2. **定义 Repository** - 继承 BaseRepository 或 TreeRepository
3. **定义 Service** - 继承 BaseService 或 TreeServiceMixin
4. **定义 API** - 继承 BaseAPI 或 TreeAPI

## 后续步骤

生成模块后：

1. 在 `src/register.py` 中注册路由
2. 运行代码检查：`ruff format . && ruff check .`
3. 生成数据库迁移：`./scripts/generate_migration.sh 'Add {module} module'`
4. 运行迁移：`./scripts/migrate.sh upgrade`

## 技能打包

```bash
# 打包技能（需要 skill-creator 的 package_skill.py）
python scripts/package_skill.py wes-module-creator-skill
```

## 许可证

MIT
