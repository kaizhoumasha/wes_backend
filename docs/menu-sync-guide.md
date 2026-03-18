# 菜单同步指南

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
menus 表
```

## 快速使用

在后端项目根目录执行：

```bash
# 仅预览 router 会生成哪些菜单
uv run python scripts/sync_menus_from_frontend.py --preview

# 对比前端 router 与数据库，不写入
uv run python scripts/sync_menus_from_frontend.py --dry-run

# 实际同步
uv run python scripts/sync_menus_from_frontend.py
```

也可以使用便捷脚本：

```bash
bash scripts/sync_menus.sh --preview
bash scripts/sync_menus.sh --dry-run
bash scripts/sync_menus.sh
```

如果前端目录不在默认位置，可显式指定：

```bash
uv run python scripts/sync_menus_from_frontend.py --frontend-path ~/SynologyDrive/works/wes_frontend
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
      icon: 'User',
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

## 种子初始化

空库初始化时，可直接从前端 router 注入菜单：

```bash
uv run python -m migrations.seed_data.seed_menus --use-frontend-data
```

如果前端目录不可用，则会回退到脚本内置的默认菜单数据。

## 已修复的问题

这套机制同时解决了旧方案中的几个问题：

- 不再依赖不存在的 `scripts/generate-menu-data.ts`
- 不再依赖不存在的 `src/router/menu_data.json`
- 统一使用 router 作为菜单注入源头，避免前后端各维护一份菜单清单
- 预览模式不再要求先初始化数据库
- `seed_menus.py` 现在会正确传递 `--use-frontend-data`
