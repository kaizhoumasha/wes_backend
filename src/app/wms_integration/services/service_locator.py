"""WMS Integration Service Locator."""

from src.database.db import get_db_context

from .typed_ports import WmsTypedPortService

wms_typed_port_service = WmsTypedPortService(session_factory=get_db_context)
