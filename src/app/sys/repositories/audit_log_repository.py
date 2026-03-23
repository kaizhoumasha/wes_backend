from src.app.sys.models.audit_log import AuditLog
from src.database.base_repository import BaseRepository

audit_log_repository = BaseRepository(AuditLog)
