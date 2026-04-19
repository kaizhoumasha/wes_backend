from types import SimpleNamespace

from src.app.admin.models import MenuTreeResponse
from src.app.admin.models.menu import Menu, MenuTreeResponseSimple
from src.app.admin.models.role import Role
from src.app.admin.services.menu_service import MenuService


def _menu(
    menu_id: int,
    *,
    parent_id: int | None,
    sort_order: int,
    title: str,
    path: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=menu_id,
        name=f"menu:{menu_id}",
        title=title,
        path=path,
        component=None,
        icon=None,
        parent_id=parent_id,
        tree_path="/",
        level=1 if parent_id is None else 2,
        sort_order=sort_order,
        version=0,
        roles=[],
        is_hidden=False,
    )


def test_build_tree_returns_menu_tree_response_with_children() -> None:
    service = MenuService()
    menus = [
        _menu(4, parent_id=1, sort_order=20, title="Child B", path="/root-a/child-b"),
        _menu(2, parent_id=None, sort_order=20, title="Root B", path="/root-b"),
        _menu(3, parent_id=1, sort_order=10, title="Child A", path="/root-a/child-a"),
        _menu(1, parent_id=None, sort_order=10, title="Root A", path="/root-a"),
    ]

    tree = service._build_tree(menus)

    assert all(isinstance(node, MenuTreeResponseSimple) for node in tree)
    assert [node.id for node in tree] == [1, 2]
    assert [child.id for child in tree[0].children] == [3, 4]


def test_build_tree_uses_id_as_stable_tie_breaker_when_sort_order_matches() -> None:
    service = MenuService()
    menus = [
        _menu(22, parent_id=9, sort_order=0, title="Child B", path="/root/child-b"),
        _menu(10, parent_id=None, sort_order=0, title="Root B", path="/root-b"),
        _menu(21, parent_id=9, sort_order=0, title="Child A", path="/root/child-a"),
        _menu(9, parent_id=None, sort_order=0, title="Root A", path="/root-a"),
    ]

    tree = service._build_tree(menus)

    assert [node.id for node in tree] == [9, 10]
    assert [child.id for child in tree[0].children] == [21, 22]


def test_build_tree_keeps_orphan_node_visible_as_root() -> None:
    service = MenuService()
    menus = [
        _menu(9, parent_id=999, sort_order=5, title="Orphan", path="/orphan"),
    ]

    tree = service._build_tree(menus)

    assert len(tree) == 1
    assert tree[0].id == 9


def test_to_dict_uses_schema_serialization_to_keep_roles_for_tree_schema() -> None:
    service = MenuService()
    menu = Menu(id=1, name="menu:1", title="Root", path="/root")
    object.__setattr__(menu, "roles", [Role(id=7, name="admin", description="管理员")])

    result = service._to_dict(menu, MenuTreeResponse)

    assert len(result["roles"]) == 1
    assert result["roles"][0]["id"] == 7
    assert result["roles"][0]["name"] == "admin"
    assert result["children"] == []


def test_menu_tree_response_preserves_recursive_children() -> None:
    payload = {
        "id": 1,
        "name": "menu:1",
        "title": "Root",
        "path": "/root",
        "component": None,
        "icon": None,
        "parent_id": None,
        "tree_path": "/1/",
        "level": 1,
        "sort_order": 10,
        "has_children": True,
        "version": 0,
        "roles": [],
        "is_hidden": False,
        "children": [
            {
                "id": 2,
                "name": "menu:2",
                "title": "Child",
                "path": "/root/child",
                "component": None,
                "icon": None,
                "parent_id": 1,
                "tree_path": "/1/2/",
                "level": 2,
                "sort_order": 20,
                "has_children": True,
                "version": 0,
                "roles": [],
                "is_hidden": False,
                "children": [
                    {
                        "id": 3,
                        "name": "menu:3",
                        "title": "Grandchild",
                        "path": "/root/child/grandchild",
                        "component": None,
                        "icon": None,
                        "parent_id": 2,
                        "tree_path": "/1/2/3/",
                        "level": 3,
                        "sort_order": 30,
                        "has_children": True,
                        "version": 0,
                        "roles": [],
                        "is_hidden": False,
                        "children": [
                            {
                                "id": 4,
                                "name": "menu:4",
                                "title": "Great Grandchild",
                                "path": "/root/child/grandchild/great-grandchild",
                                "component": None,
                                "icon": None,
                                "parent_id": 3,
                                "tree_path": "/1/2/3/4/",
                                "level": 4,
                                "sort_order": 40,
                                "has_children": False,
                                "version": 0,
                                "roles": [],
                                "is_hidden": False,
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }

    result = MenuTreeResponse.model_validate(payload)
    dumped = result.model_dump(mode="json")

    assert result.children[0].children[0].children[0].id == 4
    assert dumped["children"][0]["children"][0]["children"][0]["id"] == 4
