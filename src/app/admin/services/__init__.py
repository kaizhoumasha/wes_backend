"""
服务层模块

包含业务逻辑服务，分离路由和业务逻辑
"""
from .user_service import UserService

__all__ = ["UserService"]
