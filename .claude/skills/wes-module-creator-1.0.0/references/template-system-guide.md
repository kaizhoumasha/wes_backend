# WES Backend 模板系统使用指南

本指南介绍如何使用基于 Jinja2 的模板系统创建 WES Backend 模块。

## 概述

模板系统提供：

- ✅ **灵活的代码生成**：基于 Jinja2 模板，支持高度自定义
- ✅ **YAML 配置文件**：通过配置文件定义模块结构
- ✅ **自动化更新**：自动更新 `migrations/env.py` 和 `src/register.py`
- ✅ **字段定义支持**：支持枚举、外键、索引等复杂字段
- ✅ **关系定义**：支持一对一、一对多、多对多关系

## 目录结构

```
scripts/
├── templates/                    # Jinja2 模板目录
│   └── module/
│       ├── models/
│       │   ├── model.py.j2      # 平面结构模型
│       │   └── tree_model.py.j2 # 树形结构模型
│       ├── repositories/
│       │   └── repository.py.j2
│       ├── services/
│       │   └── service.py.j2
│       └── v1/
│           └── api.py.j2
├── template_engine.py            # 模板引擎
├── module_generator_v2.py        # 基于模板的生成器
└── generate_module.py            # 原生成器（字符串拼接）

examples/
└── device_config.yaml            # 配置文件示例
```

## 使用方式

### 方式 1：命令行参数

```bash
# 基础用法
python scripts/module_generator_v2.py --name warehouse

# 指定 Mixin
python scripts/module_generator_v2.py --name product \
  --mixins DataTableMixin,EnterpriseMixin,SoftDeleteMixin

# 树形结构
python scripts/module_generator_v2.py --name category --tree

# 指定 Schema
python scripts/module_generator_v2.py --name user --app sys --schema SYS
```

### 方式 2：YAML 配置文件（推荐）

```bash
python scripts/module_generator_v2.py --config examples/device_config.yaml
```

**配置文件格式**：

```yaml
# 模块基本信息
module_name: device
class_name: Device
app_name: biz
description: "设备管理模块"

# 结构配置
is_tree: false
schema: BIZ

# Mixin 配置
mixins:
  - DataTableMixin
  - EnterpriseMixin
  - SoftDeleteMixin

# 业务字段定义
fields:
  - name: device_code
    type: str
    description: "设备编码"
    max_length: 50
    index: true

  - name: device_type
    type: str
    description: "设备类型"
    max_length: 50
    enum: true
    enum_name: "DeviceType"
    enum_values:
      - PDA
      - PRINTER

  - name: work_line_id
    type: int | None
    description: "所属作业线 ID"
    optional: true
    foreign_key: "wes_biz.work_lines.id"

# 关系定义
relationships:
  - name: work_line
    target_model: WorkLine
    back_populates: devices
    sa_relationship_kwargs: '{"lazy": "selectin"}'

# Response Schema 中的关系
response_relationships:
  - name: work_line
    type: WorkLineResponse | None = None
```

## 字段定义

### 基础字段属性

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | str | ✅ | 字段名称（snake_case） |
| `type` | str | ✅ | 字段类型 |
| `description` | str | ✅ | 字段描述 |

### 可选字段属性

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_length` | int | - | 字符串最大长度 |
| `min_length` | int | - | 字符串最小长度 |
| `default` | Any | - | 默认值 |
| `optional` | bool | false | 是否可选 |
| `index` | bool | false | 是否创建索引 |
| `unique` | bool | false | 是否唯一 |
| `foreign_key` | str | - | 外键（格式：schema.table.column） |
| `enum` | bool | false | 是否枚举类型 |
| `enum_name` | str | - | 枚举类名 |
| `enum_values` | list | - | 枚举值列表 |

### 字段类型示例

```yaml
# 字符串
- name: code
  type: str
  max_length: 50
  index: true

# 可选字符串
- name: description
  type: str | None
  max_length: 500
  optional: true

# 整数
- name: quantity
  type: int
  default: 0

# 可选整数（外键）
- name: work_line_id
  type: int | None
  optional: true
  foreign_key: "wes_biz.work_lines.id"

# 布尔值
- name: is_active
  type: bool
  default: true

# 枚举
- name: status
  type: str
  enum: true
  enum_name: "StatusType"
  enum_values:
    - DRAFT
    - CONFIRMED
    - COMPLETED
```

## 关系定义

### 一对多关系

```yaml
# 父表（WorkLine）
relationships:
  - name: devices
    target_model: Device
    back_populates: work_line
    sa_relationship_kwargs: '{"cascade": "all, delete-orphan"}'

# 子表（Device）
relationships:
  - name: work_line
    target_model: WorkLine
    back_populates: devices
```

### 多对多关系

```yaml
# 关联表（需要在 models 中手动定义）
# user_roles.py
# class UserRole(SQLModel, table=True):
#     user_id: int = Field(foreign_key="wes_sys.users.id", primary_key=True)
#     role_id: int = Field(foreign_key="wes_sys.roles.id", primary_key=True)

# User 模型
relationships:
  - name: roles
    target_model: Role
    back_populates: users
    link_model: UserRole

# Role 模型
relationships:
  - name: users
    target_model: User
    back_populates: roles
    link_model: UserRole
```

## 自动化功能

### 1. 自动更新 migrations/env.py

生成器会自动在 `migrations/env.py` 中添加模型导入：

```python
# migrations/env.py
from src.app.biz.device.models import Device  # noqa: F401
```

### 2. 自动更新 src/register.py

生成器会自动在 `src/register.py` 中注册路由：

```python
# src/register.py
from src.app.biz.device import router_v1 as device_router

def register_routers(app: FastAPI) -> None:
    # ...
    app.include_router(device_router, prefix=settings.API_PATH)
```

## 高级用法

### 自定义模板

如果需要自定义模板，可以：

1. 复制 `templates/` 目录
2. 修改模板文件
3. 指定自定义模板目录：

```python
from template_engine import TemplateEngine

engine = TemplateEngine(template_dir=Path("custom_templates"))
```

### 批量生成模块

```bash
# 生成多个模块
for module in warehouse product inventory; do
  python scripts/module_generator_v2.py --name $module --app biz
done
```

### 与 CI/CD 集成

```yaml
# .github/workflows/generate_module.yml
name: Generate Module

on:
  workflow_dispatch:
    inputs:
      module_name:
        required: true
        type: string

jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Generate module
        run: |
          python scripts/module_generator_v2.py --name ${{ inputs.module_name }}
      - name: Run linting
        run: |
          ruff format .
          ruff check .
```

## 故障排查

### 问题 1：模板未找到

**症状**：`TemplateNotFound: template.py.j2`

**解决**：确保模板文件存在于 `scripts/templates/` 目录中

### 问题 2：Jinja2 语法错误

**症状**：`Jinja2 SyntaxError`

**解决**：检查模板语法，特别是 `{% endif %}` 和 `{{ }}` 的配对

### 问题 3：自动更新失败

**症状**：`⚠️ 无法在 migrations/env.py 中找到导入区域`

**解决**：手动添加导入语句

## 最佳实践

1. **使用 YAML 配置文件**：更清晰、更易维护
2. **定义字段时添加描述**：自动生成文档
3. **使用枚举类型**：避免魔法字符串
4. **指定索引**：提高查询性能
5. **定义关系**：自动生成正确的 SQLAlchemy 关系

## 参考资料

- **Jinja2 文档**：https://jinja.palletsprojects.com/
- **SQLModel 文档**：https://sqlmodel.tiangolo.com/
- **项目模板系统**：`scripts/templates/`
- **配置示例**：`examples/device_config.yaml`
