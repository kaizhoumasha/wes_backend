"""角色 Repository"""

from src.app.admin.models import Role
from src.database.base_repository import BaseRepository

role_repository = BaseRepository(Role)
