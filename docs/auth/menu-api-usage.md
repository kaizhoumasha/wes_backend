# 前端菜单 API 使用指南

## 功能概述

后端提供了菜单 API 端点，用于前端动态路由生成：

- **`GET /api/v1/auth/menu`** - 获取当前用户的菜单树（基于用户权限过滤）
- **`GET /api/v1/permissions/tree`** - 获取权限树形结构（管理界面使用，支持根节点和深度过滤）

## API 端点

### 1. 获取当前用户菜单（推荐）

```bash
curl -X 'GET' \
  'http://localhost:8001/api/v1/auth/menu?include_hidden=false' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer YOUR_TOKEN'
```

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "id": 1,
      "name": "admin:system",
      "title": "系统管理",
      "path": "/admin",
      "icon": "SettingOutlined",
      "component": null,
      "redirect": "/admin/users",
      "type": "menu",
      "parent_id": null,
      "level": 1,
      "sort_order": 1,
      "is_hidden": false,
      "is_cached": true,
      "is_affix": false,
      "is_external": false,
      "is_active": true,
      "children": [
        {
          "id": 2,
          "name": "admin:user:list",
          "title": "用户管理",
          "path": "/admin/users",
          "icon": "UserOutlined",
          "component": "views/admin/UserList.vue",
          "type": "menu",
          "parent_id": 1,
          "level": 2,
          "sort_order": 1,
          "children": [],
          "route_config": {
            "path": "/admin/users",
            "name": "admin:user:list",
            "component": "views/admin/UserList.vue",
            "meta": {
              "title": "用户管理",
              "icon": "UserOutlined",
              "hidden": false,
              "keepAlive": true,
              "affix": false,
              "orderNo": 1
            }
          },
          "breadcrumb": [
            {
              "title": "系统管理",
              "name": "admin:system",
              "path": "/admin",
              "icon": "SettingOutlined"
            },
            {
              "title": "用户管理",
              "name": "admin:user:list",
              "path": "/admin/users",
              "icon": "UserOutlined"
            }
          ]
        }
      ],
      "route_config": {
        "path": "/admin",
        "name": "admin:system",
        "redirect": "/admin/users",
        "meta": {
          "title": "系统管理",
          "icon": "SettingOutlined",
          "hidden": false,
          "keepAlive": true,
          "affix": false,
          "orderNo": 1
        },
        "children": [...]
      }
    }
  ]
}
```

### 2. 获取权限树（管理界面）

```bash
# 获取完整权限树
curl -X 'GET' \
  'http://localhost:8001/api/v1/permissions/tree' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer YOUR_TOKEN'

# 指定根节点和最大深度
curl -X 'GET' \
  'http://localhost:8001/api/v1/permissions/tree?root_id=1&max_depth=2' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer YOUR_TOKEN'
```

**权限树说明**：
- 返回所有类型（api/menu/button）的权限树形结构
- 支持通过 `root_id` 参数指定根节点
- 支持 `max_depth` 参数限制树深度
- 如果只需要菜单类型，可在前端通过 `type === 'menu'` 过滤

## 权限规则

菜单 API 的权限过滤逻辑：

1. **超级用户**：拥有 `*` 权限的用户可以访问所有菜单（无需检查）
2. **直接权限**：用户拥有该菜单的权限名称（如 `admin:user:list`）
3. **子菜单权限**：如果用户拥有子菜单权限，自动包含父菜单
4. **隐藏菜单**：默认不返回，可通过 `include_hidden=true` 参数返回
5. **激活状态**：只返回 `is_active=true` 的菜单

**超级用户说明**：
- 超级用户是指在权限中拥有 `*` 权限标识的用户
- 超级用户可以访问系统中的所有菜单，不受权限限制
- 超级用户检查在权限过滤的最早期执行，性能最优

## 前端集成

### Vue 3 + TypeScript 示例

```typescript
// types/menu.ts
export interface MenuItem {
  id: number
  name: string
  title: string
  path: string
  icon?: string
  component?: string
  redirect?: string
  parent_id: number | null
  level: number
  sort_order: number
  is_hidden: boolean
  is_cached: boolean
  is_affix: boolean
  is_external: boolean
  children: MenuItem[]
  route_config: RouteConfig
  breadcrumb: BreadcrumbItem[]
}

export interface RouteConfig {
  path: string
  name: string
  component?: string
  redirect?: string
  meta: {
    title: string
    icon?: string
    hidden: boolean
    keepAlive: boolean
    affix: boolean
    orderNo: number
  }
  children?: RouteConfig[]
}

// api/menu.ts
import axios from 'axios'
import type { MenuItem } from '@/types/menu'

export async function getUserMenu(includeHidden = false): Promise<MenuItem[]> {
  const { data } = await axios.get('/api/v1/auth/menu', {
    params: { include_hidden: includeHidden }
  })
  return data.data
}

// router/index.ts
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { getUserMenu } from '@/api/menu'

export async function setupDynamicRoutes() {
  const router = createRouter({
    history: createWebHistory(),
    routes: [] // 初始为空
  })

  // 获取用户菜单
  const menus = await getUserMenu()

  // 转换为 Vue Router 格式
  const routes: RouteRecordRaw[] = menus.map(menu => menu.route_config)

  // 动态添加路由
  routes.forEach(route => {
    router.addRoute(route)
  })

  return router
}
```

### React Router 示例

```typescript
// api/menu.ts
import axios from 'axios'
import type { MenuItem } from '@/types/menu'

export async function getUserMenu(): Promise<MenuItem[]> {
  const { data } = await axios.get('/api/v1/auth/menu')
  return data.data
}

// router/setup.tsx
import { createBrowserRouter } from 'react-router-dom'
import { getUserMenu } from '@/api/menu'

export async function createRouter() {
  const menus = await getUserMenu()

  // 转换为 React Router 格式
  const routes = menus.map(menu => ({
    path: menu.route_config.path,
    element: loadComponent(menu.route_config.component),
    children: menu.route_config.children?.map(child => ({
      path: child.path,
      element: loadComponent(child.component)
    }))
  }))

  return createBrowserRouter(routes)
}
```

## 响应字段说明

### PermissionTree 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 权限 ID |
| `name` | `string` | 权限标识（如 `admin:user:list`） |
| `title` | `string` | 菜单标题（用于显示） |
| `path` | `string` | 路由路径 |
| `icon` | `string?` | 菜单图标 |
| `component` | `string?` | 前端组件路径 |
| `redirect` | `string?` | 重定向路径 |
| `parent_id` | `number?` | 父菜单 ID |
| `level` | `number` | 菜单层级 |
| `sort_order` | `number` | 排序号 |
| `is_hidden` | `boolean` | 是否隐藏 |
| `is_cached` | `boolean` | 是否缓存（keepAlive） |
| `is_affix` | `boolean` | 是否固定标签页 |
| `is_external` | `boolean` | 是否外部链接 |
| `children` | `PermissionTree[]` | 子菜单列表 |
| `route_config` | `RouteConfig` | Vue Router 配置对象 |
| `breadcrumb` | `BreadcrumbItem[]` | 面包屑导航数据 |

### RouteConfig 对象

可以直接用于前端路由生成：

```typescript
{
  path: "/admin/users",
  name: "admin:user:list",
  component: "views/admin/UserList.vue",
  redirect?: "/admin/users",
  meta: {
    title: "用户管理",
    icon: "UserOutlined",
    hidden: false,
    keepAlive: true,
    affix: false,
    orderNo: 1
  },
  children: [...]
}
```

## 测试命令

以下示例使用的是开发/测试初始化数据中的默认账号，仅适用于本地或测试环境；生产环境请使用手动 bootstrap 后的管理员账号。

```bash
# 1. 登录获取 token
TOKEN=$(curl -X POST 'http://localhost:8001/api/v1/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' \
  | jq -r '.data.access_token')

# 2. 获取用户菜单
curl -X 'GET' \
  "http://localhost:8001/api/v1/auth/menu" \
  -H "Authorization: Bearer $TOKEN" \
  | jq

# 3. 获取权限树（管理用）
curl -X 'GET' \
  "http://localhost:8001/api/v1/permissions/tree" \
  -H "Authorization: Bearer $TOKEN" \
  | jq
```

## 常见问题

### Q: 为什么有些菜单不显示？
A: 检查：
1. 用户是否拥有该菜单的权限
2. 菜单的 `is_active` 是否为 `true`
3. 菜单的 `is_hidden` 是否为 `false`（或使用 `include_hidden=true`）

### Q: 如何处理外部链接？
A: 检查 `is_external` 和 `external_url` 字段：
```typescript
if (menu.is_external && menu.external_url) {
  // 使用 window.open 或 <a target="_blank">
}
```

### Q: 如何实现面包屑导航？
A: 使用 `breadcrumb` 字段：
```typescript
<Breadcrumb>
  {menu.breadcrumb.map(item => (
    <Breadcrumb.Item key={item.name}>
      <Icon type={item.icon} />
      {item.title}
    </Breadcrumb.Item>
  ))}
</Breadcrumb>
```

## 性能优化建议

1. **缓存菜单数据**：菜单变化不频繁，建议前端缓存
2. **按需加载组件**：使用懒加载 `() => import('./views/UserList.vue')`
3. **请求限流**：避免频繁请求菜单 API
4. **使用 CDN**：对于大型应用，可考虑将菜单数据缓存到 CDN
