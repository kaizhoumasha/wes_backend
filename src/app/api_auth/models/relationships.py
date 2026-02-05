from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Column, ForeignKey, Table

from src.database.db import Base
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.admin.models.perm import Permission

api_app_permissions = Table(
    "api_app_permissions",
    Base.metadata,
    Column("app_id", BigInteger, ForeignKey("wes_sys.api_applications.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", BigInteger, ForeignKey("wes_sys.permissions.id", ondelete="CASCADE"), primary_key=True),
    schema=SchemaType.SYS.value,
)
