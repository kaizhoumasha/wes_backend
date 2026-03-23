from types import SimpleNamespace

from src.app.admin.models.menu import MenuTreeResponseSimple
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


def test_build_tree_keeps_orphan_node_visible_as_root() -> None:
    service = MenuService()
    menus = [
        _menu(9, parent_id=999, sort_order=5, title="Orphan", path="/orphan"),
    ]

    tree = service._build_tree(menus)

    assert len(tree) == 1
    assert tree[0].id == 9
