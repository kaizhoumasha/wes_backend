from src.app.api_auth.models import APIApplication
from src.database.base_repository import BaseRepository


class APIAppRepository(BaseRepository[APIApplication]):
    pass


api_app_repository = APIAppRepository(APIApplication)
