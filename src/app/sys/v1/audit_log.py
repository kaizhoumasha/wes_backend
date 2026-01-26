"""
审计日志 API
"""

from src.app.sys.models.audit_log import AuditLog, AuditLogCreate, AuditLogResponse
from src.app.sys.services.audit_service import audit_log_service
from src.core.base_api import BaseAPI

audit_log_api = BaseAPI(
    module_name="sys",
    model=AuditLog,
    service=audit_log_service,
    create_schema=AuditLogCreate,
    response_schema=AuditLogResponse,
    prefix="/audit-logs",
    tags=["审计日志"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
    enable_permission=True,
    max_depth=2,
)

router = audit_log_api.router
