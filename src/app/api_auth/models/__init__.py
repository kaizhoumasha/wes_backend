from sqlalchemy.orm import relationship

from src.app.admin.models import Permission
from src.app.api_auth.models.api_access_log import (
    APIAccessLog,
    APIAccessLogBase,
    APIAccessLogCreate,
    APIAccessLogResponse,
    APIAccessLogUpdate,
)
from src.app.api_auth.models.api_application import (
    APIApplication,
    APIApplicationBase,
    APIApplicationCreate,
    APIApplicationResponse,
    APIApplicationUpdate,
)
from src.app.api_auth.models.relationships import api_app_permissions

# ==================== 处理循环引用 ====================
# APIApplication-Permission 多对多关系需要在两个模型都定义后建立。
APIApplication.permissions = relationship(
    Permission,
    secondary=api_app_permissions,
)

__all__ = [
    "APIAccessLog",
    "APIAccessLogBase",
    "APIAccessLogCreate",
    "APIAccessLogResponse",
    "APIAccessLogUpdate",
    "APIApplication",
    "APIApplicationBase",
    "APIApplicationCreate",
    "APIApplicationResponse",
    "APIApplicationUpdate",
    "api_app_permissions",
]
