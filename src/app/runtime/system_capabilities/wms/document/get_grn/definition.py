from src.app.runtime.system_capabilities.wms.query_definition import build_wms_query_capability_definition
from src.app.wms_integration.ports.document_operations import GET_GRN

DEFINITION = build_wms_query_capability_definition(GET_GRN)
