from src.app.api_auth.models import APIAccessLog
from src.database.base_repository import BaseRepository


class APIAccessLogRepository(BaseRepository[APIAccessLog]):
    pass


api_access_log_repository = APIAccessLogRepository(APIAccessLog)
