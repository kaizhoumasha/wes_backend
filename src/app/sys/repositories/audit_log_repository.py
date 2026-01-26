from src.app.sys.models.audit_log import AuditLog
from src.database.base_repository import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """审计日志仓库"""

    def __init__(self):
        super().__init__(AuditLog)


audit_log_repository = AuditLogRepository()
