from src.app.api_auth.models import APIAccessLog
from src.app.api_auth.repositories import APIAccessLogRepository, api_access_log_repository
from src.core.base_service import BaseService


class APIAccessLogService(BaseService[APIAccessLog, APIAccessLogRepository]):
    def __init__(self):
        super().__init__(repository=api_access_log_repository, enable_cache=False)


api_access_log_service = APIAccessLogService()
