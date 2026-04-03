"""
配置热加载集成测试

验证 WorkLine.plugin_key 变更后编排器立即使用新插件，
以及 plugin_class 不是缓存属性（防止未来 @cached_property 回归）。

测试场景：
- plugin_key 变更后下一次 _load_plugin 返回新插件实例
- WorkLine.plugin_class 是普通 @property（非缓存）
"""

import pytest

from src.app.workline.models.workline import WorkLine
from src.workline_plugins.smt_classifier.plugin import SmtClassifierPlugin


class TestPluginHotswapIntegration:
    """配置热加载集成测试"""

    def test_plugin_key_change_reflected_immediately(self):
        """
        场景：WorkLine.plugin_key 从 'smt_classifier' 改为 None
        断言：下一次访问 plugin_class 返回 None（而不是缓存值）
        """
        workline = WorkLine(
            line_code="TEST-LINE-001",
            line_name="测试工作线",
            line_type="AUTO",
            plugin_key="smt_classifier",
        )

        # 初始 plugin_key 指向 smt_classifier
        cls1 = workline.plugin_class
        assert cls1 is not None
        # 实例化验证
        plugin1 = cls1()
        assert isinstance(plugin1, SmtClassifierPlugin)

        # 模拟数据库更新：清除 plugin_key
        workline.plugin_key = None

        # 立即生效：plugin_class 返回 None
        cls2 = workline.plugin_class
        assert cls2 is None

        # 两次返回不同的值（证明没有缓存）
        assert cls1 is not cls2

    def test_workline_property_not_cached(self):
        """
        断言 WorkLine.plugin_class 是普通 @property 而非缓存属性。
        防止未来 @cached_property 或 functools.lru_cache 悄默损坏配置热加载。
        """
        workline = WorkLine(
            line_code="TEST-LINE-002",
            line_name="测试工作线2",
            line_type="AUTO",
            plugin_key="smt_classifier",
        )

        # 获取两次 plugin_class
        cls1 = workline.plugin_class
        cls2 = workline.plugin_class

        # 同一个类（类对象是单例）
        assert cls1 is cls2

        # 验证 plugin_class 是普通 @property
        import inspect

        prop = type(workline).__dict__.get("plugin_class")
        assert isinstance(prop, property), "plugin_class must be @property, not @cached_property or other decorator"

        # 额外检查：确保不是 functools.cached_property
        from functools import cached_property

        assert not isinstance(prop, cached_property), (
            "plugin_class must NOT be @cached_property — it would break config hot reload"
        )
