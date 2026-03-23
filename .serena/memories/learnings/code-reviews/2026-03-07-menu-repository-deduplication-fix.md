# ORM 实例去重最佳实践

## 问题描述

SQLAlchemy ORM 实例默认不可哈希，无法直接使用 `set()` 去重。

```python
# ❌ 错误：TypeError: unhashable type: 'Menu'
menus = set()
for menu in role.menus:
    menus.add(menu)  # 抛出异常
```

## 解决方案

使用 `dict[id, entity]` 按 ID 去重：

```python
# ✅ 正确
menu_map: dict[int, Menu] = {}
for menu in role.menus:
    if menu.id is not None:
        menu_map[menu.id] = menu  # 后面的会覆盖前面的
return list(menu_map.values())
```

## 关键点

1. **类型注解**：`dict[int, Menu]` 明确键值类型
2. **None 检查**：`menu.id is not None` 防止字典键为 None
3. **覆盖策略**：后面的值会覆盖前面的（保留最后出现的）

## 替代方案

如需保留首次出现的项：

```python
menu_map.setdefault(menu.id, menu)  # 只在键不存在时设置
```

## 日期

2026-03-07
