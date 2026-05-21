def test_plugin_context_imports_with_runtime_services_protocol() -> None:
    """PluginContext 导入不能因运行时服务 Protocol 注解阻断 API 启动。"""

    # Regression: ISSUE-004 — PluginContext schema 构建因 BinAllocator Protocol 崩溃。
    # Found by /qa on 2026-05-08
    # Report: .gstack/qa-reports/qa-report-localhost-5173-2026-05-08.md
    from src.workline_runtime.plugin_context import PluginContext
    from src.workline_runtime.services import WorklineRuntimeServices

    assert PluginContext.__name__ == "PluginContext"
    assert WorklineRuntimeServices().bin_allocator is None
