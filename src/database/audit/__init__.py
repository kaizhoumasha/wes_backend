"""
审计日志 Hook 注册器

提供审计日志 Hook 的自动注册功能
"""

from src.database.audit.hook_registrar import AuditHookRegistrar

__all__ = ["AuditHookRegistrar"]
