# 后端与前端交互需求

> **文档状态：历史设计草案 / 提案记录，非当前实现说明。**
>
> 本文保留的是一版前后端对齐需求与实现设想，包含“新增文件”“修改路由”等提案式描述，**不应直接视为仓库当前已落地实现**。请结合实际代码、[`README.md`](../../README.md)、[`CLAUDE.md`](../../CLAUDE.md) 与 [`docs/architecture/file_index.md`](../architecture/file_index.md) 交叉确认现状。

> 前端基于现有 API 实现，后端需要新增以下功能

---

## 1. 菜单/路由权限 (新增)

### 1.1 数据模型

**新增文件**: `src/app/admin/models/menu.py`

```python
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from src.core.mixins.enterprise import EnterpriseMixin
from src.core.mixins.soft_delete import SoftDeleteMixin
from sqlalchemy.orm import relationship

class Menu(EnterpriseMixin, SoftDeleteMixin):
    """菜单/路由模型"""
    __tablename__ = "menus"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False, comment="菜单标识，如 system:users")
    title = Column(String(50), nullable=False, comment="显示标题")
    path = Column(String(200), nullable=False, comment="路由路径，如 /system/users")
    component = Column(String(200), comment="组件路径，如 views/system/users.vue")
    icon = Column(String(50), comment="图标")
    parent_id = Column(Integer, ForeignKey("menus.id"), comment="父菜单ID")
    sort_order = Column(Integer, default=0, comment="排序")
    is_hidden = Column(Boolean, default=False, comment="是否隐藏")
    permission_id = Column(Integer, ForeignKey("permissions.id"), comment="关联权限")

    # 关联
    children = relationship("Menu", foreign_keys=[parent_id])
    permission = relationship("Permission")
```

### 1.2 API 端点

**新增文件**: `src/app/auth/v1/menu.py`

```python
from fastapi import APIRouter, Depends
from src.app.auth.models.menu import MenuResponse, MenuTreeResponse
from src.app.auth.services.menu_service import MenuService

router = APIRouter(prefix="/menus", tags=["菜单管理"])

@router.get(
    "",
    response_model=MenuTreeResponse,
    summary="获取当前用户的菜单树"
)
async def get_user_menus(
    current_user_id: int = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户可访问的菜单树（基于角色权限过滤）"""
    service = MenuService()
    return await service.get_user_menu_tree(db, current_user_id)
```

### 1.3 服务层

**新增文件**: `src/app/auth/services/menu_service.py`

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

class MenuService:
    async def get_user_menu_tree(
        self, db: AsyncSession, user_id: int
    ) -> MenuTreeResponse:
        # 1. 获取用户所有权限
        user_permissions = await get_user_permissions(db, user_id)

        # 2. 获取有权限访问的菜单
        query = select(Menu).where(
            Menu.is_deleted == False,
            Menu.permission_id.in_([p.id for p in user_permissions])
        ).order_by(Menu.sort_order)

        result = await db.execute(query)
        menus = result.scalars().all()

        # 3. 构建树形结构
        return self._build_tree(menus)

    def _build_tree(self, menus: list[Menu]) -> list[MenuResponse]:
        # 父菜单映射
        menu_map = {m.id: m for m in menus}
        tree = []

        for menu in menus:
            if menu.parent_id is None:
                tree.append(self._to_response(menu, menu_map))
            else:
                parent = menu_map.get(menu.parent_id)
                if parent and hasattr(parent, 'children'):
                    if not hasattr(parent, '_children_list'):
                        parent._children_list = []
                    parent._children_list.append(self._to_response(menu, menu_map))

        return tree
```

### 1.4 数据库迁移

```bash
cd /Users/kaizhou/SynologyDrive/works/wes_backend
alembic revision --autogenerate -m "add menus table"
alembic upgrade head
```

---

## 2. SSE 实时推送 (新增)

### 2.1 SSE 端点

**新增文件**: `src/app/sys/v1/events.py`

```python
from fastapi import APIRouter
from fastapi.responses import EventSourceResponse
from src.database.redis_client import redis_client
import json

router = APIRouter(prefix="/events", tags=["系统事件"])

@router.get("/stream")
async def event_stream():
    """SSE 实时事件推送"""
    async def event_generator():
        while True:
            # 从 Redis 队列获取事件
            event = await redis_client.brpop("events:stream", timeout=1)
            if event:
                _, data = event
                yield {
                    "event": "message",
                    "data": data
                }
    return EventSourceResponse(event_generator())
```

### 2.2 事件发布工具

**新增文件**: `src/utils/event_publisher.py`

```python
from src.database.redis_client import redis_client
import json

async def publish_event(event_type: str, payload: dict):
    """发布事件到 SSE 流"""
    event_data = {
        "type": event_type,
        "payload": payload,
        "timestamp": int(time.time() * 1000)
    }
    await redis_client.lpush("events:stream", json.dumps(event_data))
```

---

## 3. 路由注册

**修改**: `src/register.py`

```python
def register_routers(app: FastAPI) -> None:
    from src.app.auth import router_v1 as auth_router
    from src.app.auth.v1 import menu as menu_router  # 新增
    from src.app.sys.v1 import events as events_router  # 新增
    # ...

    # 新增路由
    auth_router.include_router(menu_router.router)
    app.include_router(events_router.router, prefix=settings.API_PATH)
```

---

## 4. 实现优先级

| 优先级 | 任务 | 文件 |
|--------|------|------|
| P0 | Menu 模型 + 迁移 | `models/menu.py` |
| P0 | 菜单端点 | `auth/v1/menu.py` |
| P1 | SSE 端点 | `sys/v1/events.py` |
| P2 | 事件发布工具 | `utils/event_publisher.py` |
