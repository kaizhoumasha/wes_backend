# 菜单同步指南

> 过渡状态：本文仅描述 frontend-owned menu convergence 实施前的旧菜单运行时维护能力，不是发布 Runbook，也不是 release compatibility/deploy gate。普通前端、后端发布不得调用本文脚本、读取前端镜像菜单产物或以菜单数量作为门禁；菜单收敛完成后本文归档。

## 概述

后端菜单数据现在直接以 `wes_frontend/src/router/index.ts` 为源头，不再依赖额外的 `menu_data.json` 生成步骤。

同步链路如下：

```text
前端 router/index.ts
    ↓
后端解析器（src/utils/frontend_menu_parser.py）
    ↓
菜单同步服务（src/app/admin/services/menu_sync_service.py）
    ↓
默认角色菜单回填（role_menus）
    ↓
menus 表
```

## 快速使用

在后端项目根目录执行：

```bash
# 仅预览 router 会生成哪些菜单
uv run python scripts/data/sync_menus.py --preview

# 对比前端 router 与数据库，不写入
uv run python scripts/data/sync_menus.py --dry-run

# 实际同步
uv run python scripts/data/sync_menus.py
```

也可以使用便捷脚本：

```bash
bash scripts/data/sync_menus.sh --preview
bash scripts/data/sync_menus.sh --dry-run
bash scripts/data/sync_menus.sh
```

同步菜单后，脚本会按系统内置角色规则自动补齐默认菜单：

- `系统管理员`：所有非隐藏菜单
- `管理员`：`admin:` 菜单 + 仪表盘
- `运营人员`：`biz:` 菜单 + 仪表盘
- `财务人员`：审计日志菜单 + 仪表盘
- `普通用户`：仅仪表盘

如果前端目录不在默认位置，可显式指定：

```bash
uv run python scripts/data/sync_menus.py --frontend-path ~/SynologyDrive/works/wes_frontend
```

## 默认解析规则

脚本会扫描 `src/router/index.ts`，只提取同时满足以下条件的路由：

- `meta.requiresAuth === true`
- 存在 `meta.title`，或 `meta.menu.title`

默认映射规则：

- `title` ← `meta.menu.title` 或 `meta.title`
- `path` ← 实际路由路径（自动拼接父子路径）
- `component` ← `() => import('...')` 中的组件路径
- `sort_order` ← 按路由声明顺序递增
- `is_hidden` ← `meta.menu.hidden` 或 `meta.hidden`
- `parent_id` ← `meta.menu.parentName` 或最近一个被解析为菜单的父路由

`name` 会按以下优先级生成：

1. `meta.menu.name`
2. 从 `meta.permission` 推导，例如 `admin:user:list` → `admin:user:menu`
3. 从路由 `name + path` 推导

## 推荐的前端约定

如需稳定控制 `name`、图标、排序、父子关系，请在前端路由里补充 `meta.menu`：

```ts
{
  path: 'admin/users',
  name: 'UserList',
  component: () => import('@/views/admin/users/UserListPage.vue'),
  meta: {
    requiresAuth: true,
    title: '用户管理',
    permission: ADMIN_PERMISSIONS.user.page,
    menu: {
      name: 'admin:user:menu',
      icon: 'ep:user',
      sortOrder: 10,
      parentName: 'admin:system:menu',
      hidden: false,
    },
  },
}
```

`meta.menu` 支持字段：

- `name`: 菜单唯一标识
- `title`: 覆盖展示标题
- `icon`: 菜单图标
- `sortOrder`: 排序值
- `parentName`: 父菜单 name
- `hidden`: 是否隐藏

## 显式维护边界

菜单收敛实施前，如旧运行环境确实需要修复菜单数据库，必须单独取得维护授权，并从明确的前端源码 checkout 先执行 preview、dry-run，再显式同步：

```bash
uv run python scripts/data/sync_menus.py
```

脚本内置数据只允许开发、测试或演示初始化。显式维护动作不得：

- 从 frontend candidate digest 提取 `menu-manifest.json`；
- 成为 frontend/backend producer 或 release orchestrator 的前置、后置步骤；
- 绑定 candidate Commit/digest、Nginx 恢复或 compatibility report；
- 创建第二条同步脚本、服务或菜单产物路径。

普通发布不改变菜单数据库。需要菜单结构变更时，进入 frontend-owned menu convergence 计划统一删除菜单持久化、API、同步脚本与本文，而不是把旧同步重新接回发布链。

## 已修复的问题

这套机制同时解决了旧方案中的几个问题：

- 不再依赖不存在的 `scripts/generate-menu-data.ts`
- 不再依赖不存在的 `src/router/menu_data.json`
- 统一使用 router 作为菜单注入源头，避免前后端各维护一份菜单清单
- 预览模式不再要求先初始化数据库
- 菜单初始化与同步入口统一到 `scripts/data/sync_menus.py`
