from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from src.database.schema_conf import get_all_schemas


def _attach_sqlite_schemas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        for schema in get_all_schemas():
            cursor.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
    finally:
        cursor.close()


def configure_sqlite_schemas(sync_engine: Engine) -> None:
    """Attach in-memory SQLite databases for all configured schemas."""

    event.listen(sync_engine, "connect", _attach_sqlite_schemas)
