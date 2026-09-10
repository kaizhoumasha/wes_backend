"""在未安装业务 extra 的基础制品中运行：python -m scripts.verify_core_plugin_installation。

只检查安装与入口边界；真实 Web/Worker 生命周期复用既有 integration owner。
"""

from importlib.util import find_spec
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for package, directory in (
        ("rough_sorter", "rough_sorter"),
        ("manual_bin_processing", "manual_bin_processing"),
        ("manual_picking", "manual-picking"),
    ):
        if find_spec(package) is not None or (root / "workline_plugins" / directory).exists():
            raise RuntimeError(f"基础制品包含业务包或源码: {package}")

    from deployment.plugin_composition import build_deployment_runtime
    from src.celery_app.async_runtime import celery_async_runtime
    from src.register import register_init

    if not callable(register_init) or celery_async_runtime is None:
        raise RuntimeError("基础 Web/Worker 入口不可用")
    try:
        build_deployment_runtime(
            enabled_plugin_keys=("rough_sorter",),
            session_factory=object(),
            transport_runtime=object(),
            device_command_service=object(),
        )
    except ModuleNotFoundError as exc:
        if exc.name != "rough_sorter":
            raise
    else:
        raise RuntimeError("启用缺失插件时未明确失败")
    print("基础制品安装隔离、入口导入和缺包拒绝检查通过")


if __name__ == "__main__":
    main()
