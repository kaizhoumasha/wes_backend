from app.admin.models import Menu
from src.app.admin.services.menu_sync_service import MenuSyncService
from src.utils.frontend_menu_parser import parse_frontend_router_menus


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
