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

## 种子初始化

空库或已有库同步菜单时，统一使用数据同步脚本：

```bash
uv run python scripts/data/sync_menus.py
```

如果前端目录不可用，脚本内置数据只允许开发/测试初始化使用；生产必须消费批准前端镜像内的
`/opt/wes/menu-manifest.json`，不得回退到默认菜单。

## 生产环境初始化顺序

生产环境不要执行 `scripts/data/seed_initial_data.py`。该脚本用于开发、测试、演示初始化，包含默认账号口令，不适合作为生产部署步骤。

生产权限初始化顺序由 `docs/devops/prod-release-deploy.md` 统一规定。权限零漂移且固定版本应用启动后、Nginx 恢复前，
从批准的前端 digest 提取菜单清单并同步：

```bash
case "${FRONTEND_IMAGE:?FRONTEND_IMAGE is required}" in
  *@sha256:*) ;;
  *) echo 'FRONTEND_IMAGE 必须是批准的 digest' >&2; exit 1 ;;
esac

manifest_container="wes_frontend_manifest_$$"
docker create --name "$manifest_container" "$FRONTEND_IMAGE" >/dev/null
docker cp "$manifest_container":/opt/wes/menu-manifest.json ./menu-manifest.json
docker rm "$manifest_container" >/dev/null

docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  cp ./menu-manifest.json api:/tmp/menu-manifest.json
docker compose --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  exec -T api /opt/venv/bin/python \
  scripts/data/sync_menus.py --manifest-path /tmp/menu-manifest.json
```

说明：

- 生产服务器不提供前端源码目录、不构建前端，也不接受默认菜单回退；唯一输入是批准的 immutable 前端镜像。
- 前后端分离维护 `.env.prod` 与 `.env.frontend.prod` 是正常做法，互不冲突。
- 本计划暂时保留菜单表和同步入口；后续 frontend-owned menu convergence 计划负责删除它们，不在这里增加双路径。
- 菜单同步失败时保持 Nginx 关闭；成功只证明菜单物化，不证明 API 授权、现场联调或业务验收。

## 已修复的问题

这套机制同时解决了旧方案中的几个问题：

- 不再依赖不存在的 `scripts/generate-menu-data.ts`
- 不再依赖不存在的 `src/router/menu_data.json`
- 统一使用 router 作为菜单注入源头，避免前后端各维护一份菜单清单
- 预览模式不再要求先初始化数据库
- 菜单初始化与同步入口统一到 `scripts/data/sync_menus.py`
