from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Table

from src.database.db import Base
from src.database.schema_conf import SchemaType

api_app_permissions = Table(
    "api_app_permissions",
    Base.metadata,
    Column("app_id", Integer, ForeignKey("wes_sys.api_applications.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("wes_sys.permissions.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime, default=lambda: datetime.now(UTC)),
    schema=SchemaType.SYS.value,
)
