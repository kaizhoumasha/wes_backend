from src.app.admin.models import Menu
from src.app.admin.services.menu_sync_service import MenuSyncService
from src.utils.frontend_menu_parser import load_frontend_router_menus, parse_frontend_router_menus


def test_parse_frontend_router_menus_infers_menu_from_current_router_style() -> None:
    source = """
import { ADMIN_PERMISSIONS } from '@/api/generated/permissions'

const routes = [
  {
    path: '/',
    redirect: '/dashboard'
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/auth/Login.vue'),
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    component: () => import('@/layouts/DefaultLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/Dashboard.vue'),
        meta: { requiresAuth: true, title: '仪表盘' }
      },
      {
        path: 'admin/users',
        name: 'UserList',
        component: () => import('@/views/admin/users/UserListPage.vue'),
        meta: {
          requiresAuth: true,
          permission: ADMIN_PERMISSIONS.user.page,
          title: '用户管理'
        }
      },
      ...(import.meta.env.DEV ? [
        {
          path: 'debug/smart-search',
          name: 'SmartSearchDebug',
          component: () => import('@/views/debug/smart-search-debug.vue'),
          meta: { requiresAuth: false, title: '智能搜索调试' }
        }
      ] : [])
    ]
  }
]
"""

    menus = parse_frontend_router_menus(source)

    assert [menu.name for menu in menus] == [
        "system:dashboard:menu",
        "admin:user:menu",
    ]
    assert [menu.path for menu in menus] == [
        "/dashboard",
        "/admin/users",
    ]
    assert menus[0].component == "views/dashboard/Dashboard.vue"
    assert menus[1].component == "views/admin/users/UserListPage.vue"
    assert menus[0].sort_order == 1
    assert menus[1].sort_order == 2


def test_parse_frontend_router_menus_respects_meta_menu_overrides() -> None:
    source = """
const routes = [
  {
    path: '/',
    component: () => import('@/layouts/DefaultLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: 'admin',
        name: 'AdminRoot',
        component: () => import('@/views/admin/AdminLayout.vue'),
        meta: {
          requiresAuth: true,
          title: '系统管理',
          menu: {
            name: 'admin:system:menu',
            icon: 'ep:setting',
            sortOrder: 10
          }
        },
        children: [
          {
            path: 'users',
            name: 'UserList',
            component: () => import('@/views/admin/users/UserListPage.vue'),
            meta: {
              requiresAuth: true,
              title: '用户管理',
              menu: {
                name: 'admin:user:menu',
                parentName: 'admin:system:menu',
                icon: 'ep:user',
                sortOrder: 11,
                hidden: true
              }
            }
          }
        ]
      }
    ]
  }
]
"""

    menus = parse_frontend_router_menus(source)

    assert [menu.name for menu in menus] == [
        "admin:system:menu",
        "admin:user:menu",
    ]
    assert menus[0].path == "/admin"
    assert menus[0].icon == "ep:setting"
    assert menus[0].sort_order == 10
    assert menus[1].path == "/admin/users"
    assert menus[1].parent_name == "admin:system:menu"
    assert menus[1].icon == "ep:user"
    assert menus[1].is_hidden is True


def test_menu_sync_service_update_payload_includes_version_when_data_changes() -> None:
    existing = Menu(
        id=1,
        name="admin:user:menu",
        title="用户管理",
        path="/admin/users",
        component="views/admin/users/UserListPage.vue",
        icon="ep:user",
        parent_id=None,
        sort_order=10,
        is_hidden=False,
        version=7,
    )

    payload = {
        "title": "用户管理",
        "path": "/admin/users",
        "component": "views/admin/users/UserListPage.vue",
        "icon": "ep:user-filled",
        "parent_id": None,
        "sort_order": 10,
        "is_hidden": False,
    }

    update_data = MenuSyncService._build_update_data(existing, payload)

    assert update_data["icon"] == "ep:user-filled"
    assert update_data["version"] == 7


def test_load_frontend_router_menus_discovers_route_modules_from_routes_index(tmp_path) -> None:
    frontend_root = tmp_path / "frontend"
    router_dir = frontend_root / "src" / "router"
    routes_dir = router_dir / "routes"
    routes_dir.mkdir(parents=True)

    (router_dir / "index.ts").write_text(
        """
import { createRouter, createWebHistory } from 'vue-router'
import { createRoutes } from './routes'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: createRoutes()
})

export default router
""",
        encoding="utf-8",
    )
    (routes_dir / "index.ts").write_text(
        """
import { futureRoutes } from './future'
import { shellBaseChildren } from './base'

export function createRoutes() {
  return [
    ...shellBaseChildren,
    futureRoutes
  ]
}
""",
        encoding="utf-8",
    )
    (routes_dir / "base.ts").write_text(
        """
export const shellBaseChildren = [
  {
    path: 'dashboard',
    name: 'Dashboard',
    component: () => import('@/views/dashboard/Dashboard.vue'),
    meta: { requiresAuth: true, title: '仪表盘' }
  }
]
""",
        encoding="utf-8",
    )
    (routes_dir / "future.ts").write_text(
        """
export const futureRoutes = {
  path: 'future',
  name: 'FutureRoot',
  meta: {
    requiresAuth: true,
    title: '未来模块',
    menu: {
      name: 'future:system:menu'
    }
  },
  children: [
    {
      path: 'jobs',
      name: 'FutureJobList',
      component: () => import('@/views/future/FutureJobsPage.vue'),
      meta: {
        requiresAuth: true,
        title: '未来任务',
        menu: {
          name: 'future:job:menu',
          parentName: 'future:system:menu'
        }
      }
    }
  ]
}
""",
        encoding="utf-8",
    )

    menus = load_frontend_router_menus(frontend_root)

    assert [menu.name for menu in menus] == [
        "system:dashboard:menu",
        "future:system:menu",
        "future:job:menu",
    ]
    assert [menu.sort_order for menu in menus] == [1, 2, 3]
    assert menus[-1].path == "/future/jobs"


def test_load_frontend_router_menus_expands_factory_routes_in_create_routes_order(tmp_path) -> None:
    frontend_root = tmp_path / "frontend"
    router_dir = frontend_root / "src" / "router"
    routes_dir = router_dir / "routes"
    routes_dir.mkdir(parents=True)

    (router_dir / "index.ts").write_text(
        """
import { createRouter, createWebHistory } from 'vue-router'
import { createRoutes } from './routes'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: createRoutes()
})

export default router
""",
        encoding="utf-8",
    )
    (routes_dir / "index.ts").write_text(
        """
import { createExtraRoutes } from './extra'
import { shellBaseChildren, shellRoute } from './base'

export function createRoutes() {
  return [
    {
      ...shellRoute,
      children: [
        ...shellBaseChildren,
        ...createExtraRoutes()
      ]
    }
  ]
}
""",
        encoding="utf-8",
    )
    (routes_dir / "base.ts").write_text(
        """
export const shellBaseChildren = [
  {
    path: 'dashboard',
    name: 'Dashboard',
    component: () => import('@/views/dashboard/Dashboard.vue'),
    meta: { requiresAuth: true, title: '仪表盘' }
  }
]

export const shellRoute = {
  path: '/',
  component: () => import('@/layouts/DefaultLayout.vue'),
  meta: { requiresAuth: true },
  children: []
}
""",
        encoding="utf-8",
    )
    (routes_dir / "extra.ts").write_text(
        """
export function createExtraRoutes() {
  return [
    {
      path: 'ops',
      name: 'OpsRoot',
      meta: {
        requiresAuth: true,
        title: '运维中心',
        menu: {
          name: 'ops:system:menu'
        }
      },
      children: [
        {
          path: 'alerts',
          name: 'OpsAlertList',
          component: () => import('@/views/ops/AlertsPage.vue'),
          meta: {
            requiresAuth: true,
            title: '告警面板',
            menu: {
              name: 'ops:alert:menu',
              parentName: 'ops:system:menu'
            }
          }
        }
      ]
    }
  ]
}
""",
        encoding="utf-8",
    )

    menus = load_frontend_router_menus(frontend_root)

    assert [menu.name for menu in menus] == [
        "system:dashboard:menu",
        "ops:system:menu",
        "ops:alert:menu",
    ]
    assert [menu.path for menu in menus] == [
        "/dashboard",
        "/ops",
        "/ops/alerts",
    ]


def test_load_frontend_router_menus_prefers_generated_manifest(tmp_path) -> None:
    frontend_root = tmp_path / "frontend"
    artifacts_dir = frontend_root / "artifacts"
    router_dir = frontend_root / "src" / "router"
    artifacts_dir.mkdir(parents=True)
    router_dir.mkdir(parents=True)

    (artifacts_dir / "menu-manifest.json").write_text(
        """
[
  {
    "name": "runtime:system:menu",
    "title": "运行监控中心",
    "path": "/runtime",
    "component": null,
    "sortOrder": 30,
    "parentName": null,
    "icon": "ep:monitor",
    "isHidden": false,
    "permission": null
  },
  {
    "name": "runtime:worklines:menu",
    "title": "工作线监控",
    "path": "/runtime/worklines",
    "component": "views/runtime/worklines/WorklineRuntimePage.vue",
    "sortOrder": 3,
    "parentName": "runtime:system:menu",
    "icon": "ep:share",
    "isHidden": false,
    "permission": "biz:workline:list"
  }
]
""",
        encoding="utf-8",
    )

    (router_dir / "index.ts").write_text("const routes = [", encoding="utf-8")

    menus = load_frontend_router_menus(frontend_root)

    assert [menu.name for menu in menus] == [
        "runtime:system:menu",
        "runtime:worklines:menu",
    ]
    assert menus[1].path == "/runtime/worklines"
    assert menus[1].component == "views/runtime/worklines/WorklineRuntimePage.vue"
