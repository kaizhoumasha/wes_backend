"""为隔离的 SQLite 测试显式注册跨域 SQLModel 外键目标。"""

from __future__ import annotations


def register_required_sqlmodel_metadata() -> None:
    """加载测试共享 metadata 中跨 runtime、sys、device、workline 的外键目标。"""
    from src.app.device.models.command import DeviceCommand
    from src.app.device.models.device import Device
    from src.app.execution.models import BinExecution, PositionProjection
    from src.app.runtime.orchestration.models.diagnostic import WorklineDiagnostic
    from src.app.runtime.orchestration.models.session import WorklineSession
    from src.app.runtime.orchestration.models.timeline import WorklineTimeline
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
    from src.app.sys.models import SystemOutbox
    from src.app.workline.models.line_run_epoch import LineRunEpoch
    from src.app.workline.models.workline import WorkLine

    # SQLModel 在类导入时向共享 metadata 注册表；保留显式引用以说明这是有意的副作用导入。
    _ = (
        BinExecution,
        Device,
        DeviceCommand,
        LineRunEpoch,
        PositionProjection,
        RuntimeInbox,
        SystemOutbox,
        WorkLine,
        WorklineDiagnostic,
        WorklineSession,
        WorklineTimeline,
    )
