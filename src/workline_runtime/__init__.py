"""
WES 作业线运行时模块

本模块提供作业线插件化编排的核心基础设施：
- 统一输入模型（Inbox）
- 副作用派发（Outbox）
- 会话管理（Session）
- 时间线追踪（Timeline）
- 决策记录（Decision）
- 外部调用日志（ExternalCall）

设计原则：
- DRY: 横切能力统一实现，不散落到插件中
- KISS: 使用 Python 类和显式状态机，避免复杂 DSL
- SOLID: 分层职责清晰，插件不直接操作基础设施
- YAGNI: 仅实现当前业务明确需要的能力
"""

__version__ = "1.0.0"
