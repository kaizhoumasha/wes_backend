import json
from pathlib import Path

from src.app.wms_adapter.transport_openapi import build_transport_openapi_document


def test_standalone_transport_openapi_303_artifact_is_generated_from_the_shared_builder() -> None:
    artifact_path = Path(__file__).resolve().parents[3] / "docs/contracts/openapi/wes-wms-transport.openapi.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert artifact == build_transport_openapi_document()
    assert artifact["openapi"] == "3.0.3"
    assert set(artifact["paths"]) == {"/api/v1/wms/events"}
    assert "429" not in artifact["paths"]["/api/v1/wms/events"]["post"]["responses"]
    assert artifact["paths"]["/api/v1/wms/events"]["post"]["responses"]["401"]["x-operational-error"] is True


def test_transport_openapi_limits_outcome_revision_to_signed_int64() -> None:
    document = build_transport_openapi_document()
    request_schema = document["paths"]["/api/v1/wms/events"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    revisions = [
        schema
        for schema in _walk_schemas(request_schema)
        if schema.get("type") == "integer" and schema.get("minimum") == 1
    ]

    assert revisions
    assert all(schema["format"] == "int64" for schema in revisions)
    assert all(schema["maximum"] == 2**63 - 1 for schema in revisions)


def test_transport_openapi_exposes_only_v03_callback_identity_shapes() -> None:
    serialized = json.dumps(build_transport_openapi_document(), ensure_ascii=False)

    assert '"container_id"' in serialized
    assert '"rack_id"' in serialized
    assert '"bin_id"' not in serialized
    assert '"object_id"' not in serialized
    assert '"retry_after_ms"' not in serialized
    assert '"BUSY"' not in serialized


def test_transport_openapi_models_faces_as_opaque_non_empty_strings_without_nul() -> None:
    schemas = [
        schema
        for schema in _walk_schemas(build_transport_openapi_document())
        if schema.get("description") == "Opaque non-empty face value without NUL; preserve exactly"
    ]

    assert schemas
    assert all(schema["type"] == "string" and schema["minLength"] == 1 for schema in schemas)
    assert all(schema["pattern"] == "^[^\\u0000]+$" for schema in schemas)
    assert all({"enum", "maxLength", "format"}.isdisjoint(schema) for schema in schemas)


def test_transport_openapi_closes_bin_result_member_counts_by_kind() -> None:
    document = build_transport_openapi_document()
    request_schema = document["paths"]["/api/v1/wms/events"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    bin_result_variants = {
        schema["properties"]["kind"]["enum"][0]: schema["properties"]["results"]
        for schema in _walk_schemas(request_schema)
        if schema.get("type") == "object"
        and schema.get("properties", {}).get("kind", {}).get("enum") in (["BIN_MOVE"], ["BIN_EXCHANGE"])
        and "results" in schema["properties"]
    }

    assert bin_result_variants == {
        "BIN_MOVE": {"type": "array", "minItems": 1, "maxItems": 4, "items": bin_result_variants["BIN_MOVE"]["items"]},
        "BIN_EXCHANGE": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": bin_result_variants["BIN_EXCHANGE"]["items"],
            "not": {"minItems": 3, "maxItems": 3},
        },
    }


def test_transport_openapi_allows_conflict_without_an_associated_task() -> None:
    document = build_transport_openapi_document()
    data_schema = document["paths"]["/api/v1/wms/events"]["post"]["responses"]["409"]["content"]["application/json"][
        "schema"
    ]["properties"]["data"]

    assert data_schema == {
        "oneOf": [
            {"type": "object", "additionalProperties": False, "required": [], "properties": {}},
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["transport_task_id"],
                "properties": {
                    "transport_task_id": {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r".*\S.*"}
                },
            },
        ]
    }


def _walk_schemas(schema: object):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _walk_schemas(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk_schemas(value)
