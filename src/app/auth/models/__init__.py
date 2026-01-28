"""
认证模型模块

导出所有认证相关的 Pydantic Schemas

注意：LoginResponse 引用 admin 模块的 UserResponse，
需要在此处导入并重建模型以解决跨模块引用
"""

# 导入认证模型
# ==================== 处理跨模块引用 ====================
# 导入 admin 模块的 UserResponse 来解析 LoginResponse 的前向引用

from .auth import LoginRequest, LoginResponse, RefreshTokenResponse

# 重建 LoginResponse 模型，解析 user 字段的类型注解
LoginResponse.model_rebuild()

# ==================== 导出所有公开内容 ====================

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "RefreshTokenResponse",
]
