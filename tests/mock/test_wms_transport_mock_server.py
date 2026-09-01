from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app.wms_adapter.transport_openapi import TRANSPORT_EVENT_REQUEST_SCHEMA
from src.app.wms_adapter.transport_wire import validate_callback_envelope
from tests.mock import wms_mock_server, wms_transport_mock_openapi
from tests.mock.wms_transport_mock_openapi import bin_exchange, bin_move, rack_move, rack_rotate

RACK_MOVE = deepcopy(rack_move)
RACK_ROTATE = deepcopy(rack_rotate)
BIN_MOVE = deepcopy(bin_move)
BIN_EXCHANGE = deepcopy(bin_exchange)
FIXTURE_RACK_FACES = {"rack-1": "90", "rack-2": "90"}


def setup_function() -> None:
    wms_mock_server.reset_mock_wms_state()
    wms_mock_server.transport_submission_store.configure_rack_faces(FIXTURE_RACK_FACES)


def test_docs_use_only_packaged_swagger_assets() -> None:
    with TestClient(wms_mock_server.app) as client:
        docs = client.get("/docs")
        openapi = client.get("/openapi.json")
        javascript = client.get("/static/swagger-ui/swagger-ui-bundle.js")
        stylesheet = client.get("/static/swagger-ui/swagger-ui.css")

    assert docs.status_code == 200
    assert openapi.status_code == 200
    assert javascript.status_code == 200
    assert stylesheet.status_code == 200
    assert docs.content
    assert openapi.content
    assert javascript.content
    assert stylesheet.content
    assert docs.headers["content-type"].startswith("text/html")
    assert openapi.headers["content-type"].startswith("application/json")
    assert javascript.headers["content-type"].startswith("text/javascript")
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "http://" not in docs.text
    assert "https://" not in docs.text


def test_openapi_groups_transport_contract_and_mock_debug_operations() -> None:
    document = wms_mock_server.app.openapi()

    assert [tag["name"] for tag in document["tags"]] == ["WMS Transport Contract", "Mock Debug"]
    assert document["paths"]["/api/v1/wes/transport-requests"]["post"]["tags"] == ["WMS Transport Contract"]
    assert document["paths"]["/debug/transport-callbacks"]["post"]["tags"] == ["Mock Debug"]
    assert document["paths"]["/debug/reset"]["post"]["tags"] == ["Mock Debug"]
    assert document["paths"]["/debug/transport-submit-mode"]["post"]["tags"] == ["Mock Debug"]
    assert document["paths"]["/debug/rack-faces"]["post"]["tags"] == ["Mock Debug"]
    assert document["paths"]["/debug/transport-submissions"]["get"]["tags"] == ["Mock Debug"]
    assert document["paths"]["/"]["get"]["tags"] == ["Mock Debug"]


def test_transport_submit_openapi_examples_follow_the_raw_submit_handler() -> None:
    examples = wms_transport_mock_openapi.TRANSPORT_SUBMISSION_EXAMPLES

    assert set(examples) == {"rack_move", "rack_rotate", "bin_move", "bin_exchange"}
    with TestClient(wms_mock_server.app) as client:
        responses = []
        for example in examples.values():
            client.post("/debug/reset")
            wms_mock_server.transport_submission_store.configure_rack_faces(FIXTURE_RACK_FACES)
            responses.append(client.post("/api/v1/wes/transport-requests", json=deepcopy(example["value"])))

    assert [response.status_code for response in responses] == [202, 202, 202, 202]


def test_transport_submit_openapi_schema_is_closed_and_documents_runtime_invariants() -> None:
    submit_operation = wms_mock_server.app.openapi()["paths"]["/api/v1/wes/transport-requests"]["post"]
    submit_schema = submit_operation["requestBody"]["content"]["application/json"]["schema"]

    def assert_all_object_nodes_closed(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_all_object_nodes_closed(value)
        elif isinstance(node, list):
            for value in node:
                assert_all_object_nodes_closed(value)

    assert submit_operation["tags"] == [wms_transport_mock_openapi.WMS_TRANSPORT_CONTRACT_TAG]
    assert set(submit_operation["responses"]) == {"200", "202", "400", "409", "413", "422", "503"}
    assert set(submit_operation["requestBody"]["content"]["application/json"]["examples"]) == {
        "rack_move",
        "rack_rotate",
        "bin_move",
        "bin_exchange",
    }
    assert len(submit_schema["oneOf"]) == 4
    assert_all_object_nodes_closed(submit_schema)
    descriptions_by_kind = {
        envelope["properties"]["data"]["properties"]["kind"]["enum"][0]: envelope["properties"]["data"]["description"]
        for envelope in submit_schema["oneOf"]
    }
    assert "rack bin slot 唯一" in descriptions_by_kind["BIN_MOVE"]
    assert "位置唯一" in descriptions_by_kind["BIN_EXCHANGE"]
    assert "source 与 target 不同" in descriptions_by_kind["RACK_MOVE"]
    assert "source 与 target 相同" in descriptions_by_kind["RACK_ROTATE"]
    rack_id_schema = submit_schema["oneOf"][0]["properties"]["data"]["properties"]["rack_id"]
    assert "不得包含 NUL" in rack_id_schema["description"]
    assert rack_id_schema["pattern"] == r".*\S.*"


def test_transport_submit_openapi_documents_closed_ack_unions() -> None:
    responses = wms_mock_server.app.openapi()["paths"]["/api/v1/wes/transport-requests"]["post"]["responses"]

    for status_code, code in (("200", "DUPLICATE"), ("202", "RECEIVED"), ("409", "CONFLICT"), ("503", "UNAVAILABLE")):
        schema = responses[status_code]["content"]["application/json"]["schema"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["code"]["enum"] == [code]
        assert schema["properties"]["data"]["required"] == ["transport_task_id"]
        assert schema["properties"]["data"]["additionalProperties"] is False

    rejected = responses["422"]["content"]["application/json"]["schema"]
    assert rejected["properties"]["code"]["enum"] == ["REJECTED"]
    assert len(rejected["properties"]["data"]["oneOf"]) == 2
    assert all(branch["additionalProperties"] is False for branch in rejected["properties"]["data"]["oneOf"])
    assert "content" not in responses["400"]
    assert "content" not in responses["413"]


def test_transport_callback_openapi_reuses_the_shared_request_schema() -> None:
    callback_schema = wms_mock_server.app.openapi()["paths"]["/debug/transport-callbacks"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]

    assert callback_schema == TRANSPORT_EVENT_REQUEST_SCHEMA


def test_transport_callback_examples_are_valid_and_relay_unchanged(monkeypatch) -> None:
    examples = wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES
    captured: list[dict[str, object]] = []

    async def capture(_: str, payload: dict[str, object]) -> int:
        captured.append(payload)
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", capture)
    for example in examples.values():
        validate_callback_envelope(deepcopy(example["value"]))
    with TestClient(wms_mock_server.app) as client:
        responses = [
            client.post("/debug/transport-callbacks", json=deepcopy(example["value"])) for example in examples.values()
        ]

    assert set(examples) == {"member_target_placed", "rack_succeeded", "bin_succeeded"}
    assert [response.json() for response in responses] == [{"status_code": 202}] * 3
    assert captured == [example["value"] for example in examples.values()]


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (httpx.TimeoutException("timed out"), 504, "WES_CALLBACK_TIMEOUT"),
        (httpx.RequestError("unavailable"), 502, "WES_CALLBACK_UNAVAILABLE"),
    ],
)
def test_transport_callback_maps_httpx_errors(
    monkeypatch, error: httpx.RequestError, status_code: int, code: str
) -> None:
    async def fail(_: str, __: dict[str, object]) -> int:
        raise error

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", fail)
    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/debug/transport-callbacks",
            json=wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["member_target_placed"]["value"],
        )

    assert response.status_code == status_code
    assert response.json() == {"code": code}


def test_transport_callback_rejects_malformed_json_and_non_object_before_relay() -> None:
    with TestClient(wms_mock_server.app) as client:
        malformed = client.post(
            "/debug/transport-callbacks", content=b"{", headers={"Content-Type": "application/json"}
        )
        non_object = client.post("/debug/transport-callbacks", json=[])

    assert malformed.status_code == 422
    assert non_object.status_code == 422


def test_transport_submission_snapshots_follow_the_submit_state_matrix() -> None:
    changed = deepcopy(RACK_MOVE)
    changed["data"]["target"]["location_code"] = "station-c"
    invalid = deepcopy(BIN_MOVE)
    invalid["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4476"
    invalid["data"]["moves"][0]["vehicle_id"] = "agv-private"
    task_conflict = deepcopy(RACK_MOVE)
    task_conflict["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4477"
    unavailable = deepcopy(RACK_ROTATE)
    unavailable["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4478"

    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 202
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 200
        assert client.post("/api/v1/wes/transport-requests", json=changed).status_code == 409
        assert client.post("/api/v1/wes/transport-requests", json=invalid).status_code == 422
        assert client.post("/api/v1/wes/transport-requests", json=task_conflict).status_code == 409
        assert client.post("/debug/transport-submit-mode", json={"mode": "UNAVAILABLE"}).status_code == 200
        assert client.post("/api/v1/wes/transport-requests", json=unavailable).status_code == 503
        snapshot = client.get("/debug/transport-submissions")

    assert snapshot.status_code == 200
    submissions = snapshot.json()["submissions"]
    assert [(item["operation_id"], item["status_code"]) for item in submissions] == [
        (RACK_MOVE["operation_id"], 202),
        (invalid["operation_id"], 422),
        (task_conflict["operation_id"], 409),
    ]
    assert all(
        set(item) == {"operation_id", "operation", "transport_task_id", "request", "status_code", "response"}
        for item in submissions
    )
    preserved = deepcopy(submissions)
    submissions[0]["request"]["data"]["rack_id"] = "mutated"
    with TestClient(wms_mock_server.app) as client:
        assert client.get("/debug/transport-submissions").json()["submissions"] == preserved


def test_debug_reset_clears_transport_submission_snapshots() -> None:
    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 202
        assert client.post("/debug/reset").json() == {"reset": True}
        snapshots = client.get("/debug/transport-submissions")

    assert snapshots.status_code == 200
    assert snapshots.json() == {"submissions": []}


def test_transport_submission_snapshots_have_no_second_store() -> None:
    assert not hasattr(wms_mock_server.TransportSubmissionStore(), "_snapshots")


def test_coordinated_exchange_rejection_is_snapshot_recorded_and_replays_stably() -> None:
    with TestClient(wms_mock_server.app) as client:
        client.post("/debug/transport-submit-mode", json={"mode": "COORDINATED_EXCHANGE_UNSUPPORTED"})
        first = client.post("/api/v1/wes/transport-requests", json=BIN_EXCHANGE)
        replay = client.post("/api/v1/wes/transport-requests", json=BIN_EXCHANGE)
        submissions = client.get("/debug/transport-submissions").json()["submissions"]

    assert first.status_code == 422
    assert first.json()["data"]["reason_code"] == "COORDINATED_BIN_EXCHANGE_UNSUPPORTED"
    assert replay.status_code == 422
    assert replay.json() == first.json()
    assert [(item["operation_id"], item["status_code"]) for item in submissions] == [
        (BIN_EXCHANGE["operation_id"], 422)
    ]


def test_duplicate_does_not_consume_an_armed_submit_mode() -> None:
    next_exchange = deepcopy(BIN_EXCHANGE)
    next_exchange["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4479"

    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 202
        assert (
            client.post("/debug/transport-submit-mode", json={"mode": "COORDINATED_EXCHANGE_UNSUPPORTED"}).status_code
            == 200
        )
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 200
        armed_result = client.post("/api/v1/wes/transport-requests", json=next_exchange)

    assert armed_result.status_code == 422
    assert armed_result.json()["data"]["reason_code"] == "COORDINATED_BIN_EXCHANGE_UNSUPPORTED"


def test_transport_submit_is_the_only_wms_business_route_and_requires_no_authentication() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=RACK_MOVE)

    assert response.status_code == 202
    assert response.json() == {
        "operation_id": RACK_MOVE["operation_id"],
        "code": "RECEIVED",
        "timestamp": response.json()["timestamp"],
        "data": {"transport_task_id": "transport-rack-1"},
    }
    business_routes = {
        route.path
        for route in wms_mock_server.app.routes
        if route.path.startswith("/api/") and not route.path.startswith("/api/v1/wes/transport-requests/")
    }
    assert business_routes == {"/api/v1/wes/transport-requests"}


@pytest.mark.parametrize("envelope", [RACK_ROTATE, BIN_MOVE, BIN_EXCHANGE])
def test_transport_submit_accepts_the_other_frozen_transport_shapes(envelope: dict[str, object]) -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=envelope)

    assert response.status_code == 202
    assert response.json()["code"] == "RECEIVED"
    assert response.json()["data"] == {"transport_task_id": envelope["data"]["transport_task_id"]}


@pytest.mark.parametrize(
    ("source", "target", "template"),
    [
        ({"kind": "ZONE", "location_code": "zone-1"}, {"kind": "RACK_POSITION", "location_code": "work"}, "CTU01"),
        ({"kind": "RACK", "location_code": "rack-1"}, {"kind": "RACK_POSITION", "location_code": "work"}, "CTU01"),
        ({"kind": "RACK_POSITION", "location_code": "a"}, {"kind": "RACK_POSITION", "location_code": "b"}, "CTU01"),
        ({"kind": "RACK_POSITION", "location_code": "a"}, {"kind": "RACK", "location_code": "rack-1"}, "CTU03"),
        ({"kind": "RACK_POSITION", "location_code": "a"}, {"kind": "ZONE", "location_code": "zone-1"}, "CTU03"),
        ({"kind": "RACK_POSITION", "location_code": "a"}, {"kind": "RACK_POSITION", "location_code": "b"}, "CTU03"),
        ({"kind": "RACK_POSITION", "location_code": "a"}, {"kind": "RACK_POSITION", "location_code": "b"}, "F01"),
    ],
)
def test_transport_submit_mock_accepts_the_approved_rack_move_matrix(
    source: dict[str, str], target: dict[str, str], template: str
) -> None:
    envelope = deepcopy(RACK_MOVE)
    envelope["data"]["source"] = source
    envelope["data"]["target"] = target
    envelope["data"]["rcs_template_id"] = template

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=envelope)

    assert response.status_code == 202


@pytest.mark.parametrize(
    ("source", "target", "template"),
    [
        ({"kind": "RACK", "location_code": "rack-1"}, {"kind": "ZONE", "location_code": "zone-1"}, "CTU01"),
        ({"kind": "ZONE", "location_code": "zone-1"}, {"kind": "RACK", "location_code": "rack-1"}, "CTU03"),
        ({"kind": "RACK_POSITION", "location_code": "a"}, {"kind": "RACK_POSITION", "location_code": "b"}, "CTU02"),
    ],
)
def test_transport_submit_mock_rejects_unapproved_rack_move_matrix(
    source: dict[str, str], target: dict[str, str], template: str
) -> None:
    envelope = deepcopy(RACK_MOVE)
    envelope["data"]["source"] = source
    envelope["data"]["target"] = target
    envelope["data"]["rcs_template_id"] = template

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=envelope)

    assert response.status_code == 422


@pytest.mark.parametrize("face", ["90", "270", "FACE@01", "面-1", " ", "x" * 1000])
def test_transport_submit_mock_preserves_any_non_empty_face_string(face: str) -> None:
    envelope = deepcopy(RACK_MOVE)
    envelope["data"]["target_face"] = face

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=envelope)

    assert response.status_code == 202
    assert wms_mock_server.transport_submission_store.snapshots()[-1]["request"]["data"]["target_face"] == face


@pytest.mark.parametrize("template", [RACK_MOVE, BIN_MOVE], ids=["target-face", "rack-face"])
def test_transport_submit_mock_rejects_nul_face(template: dict[str, object]) -> None:
    envelope = deepcopy(template)
    data = envelope["data"]
    assert isinstance(data, dict)
    if data["kind"] == "RACK_MOVE":
        data["target_face"] = "\x00"
    else:
        moves = data["moves"]
        assert isinstance(moves, list)
        moves[0]["source"]["rack_face"] = "\x00"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=envelope)

    assert response.status_code == 422


def test_transport_submit_mock_rejects_face_not_representable_as_utf8() -> None:
    assert wms_mock_server._opaque_face("\ud800") is False


@pytest.mark.parametrize(("field_path", "invalid_value"), [("kind", []), ("target_face", {})])
def test_transport_submit_rejects_non_string_enum_values(field_path: str, invalid_value: object) -> None:
    invalid = deepcopy(RACK_ROTATE)
    invalid["data"][field_path] = invalid_value

    with TestClient(wms_mock_server.app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/wes/transport-requests", json=invalid)

    assert response.status_code == 422
    assert response.json()["code"] == "REJECTED"
    assert response.json()["data"]["reason_code"] == "INVALID_DATA"


def test_rack_rotate_requires_a_trusted_opposite_current_face() -> None:
    with TestClient(wms_mock_server.app) as client:
        configured = client.post("/debug/rack-faces", json={"rack_faces": {"rack-2": "90"}})
        accepted = client.post("/api/v1/wes/transport-requests", json=RACK_ROTATE)
        client.post("/debug/reset")
        client.post("/debug/rack-faces", json={"rack_faces": {"rack-2": "270"}})
        same_face = client.post("/api/v1/wes/transport-requests", json=RACK_ROTATE)
        client.post("/debug/reset")
        unknown_face = client.post("/api/v1/wes/transport-requests", json=RACK_ROTATE)

    assert configured.status_code == 200
    assert accepted.status_code == 202
    assert same_face.status_code == 409
    assert same_face.json()["code"] == "CONFLICT"
    assert unknown_face.status_code == 503
    assert unknown_face.json()["code"] == "UNAVAILABLE"


def test_bin_submit_rejects_unknown_or_mismatched_current_face() -> None:
    with TestClient(wms_mock_server.app) as client:
        client.post("/debug/reset")
        unknown_face = client.post("/api/v1/wes/transport-requests", json=BIN_MOVE)
        client.post("/debug/rack-faces", json={"rack_faces": {"rack-1": "270"}})
        mismatched_face = client.post("/api/v1/wes/transport-requests", json=BIN_MOVE)

    assert unknown_face.status_code == 503
    assert unknown_face.json()["code"] == "UNAVAILABLE"
    assert mismatched_face.status_code == 409
    assert mismatched_face.json()["code"] == "CONFLICT"


def test_transport_submit_rejects_active_rack_resource_conflict() -> None:
    conflicting = deepcopy(RACK_MOVE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4490"
    conflicting["data"]["transport_task_id"] = "transport-rack-conflict"
    conflicting["data"]["target"]["location_code"] = "station-conflict"

    with TestClient(wms_mock_server.app) as client:
        first = client.post("/api/v1/wes/transport-requests", json=RACK_MOVE)
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["data"] == {"transport_task_id": "transport-rack-conflict"}


def test_transport_submit_rejects_active_container_resource_conflict() -> None:
    conflicting = deepcopy(BIN_MOVE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4491"
    conflicting["data"]["transport_task_id"] = "transport-bin-conflict"
    conflicting["data"]["moves"][0]["source"]["rack_id"] = "rack-3"

    with TestClient(wms_mock_server.app) as client:
        client.post("/debug/rack-faces", json={"rack_faces": {"rack-1": "90", "rack-3": "90"}})
        first = client.post("/api/v1/wes/transport-requests", json=BIN_MOVE)
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["data"] == {"transport_task_id": "transport-bin-conflict"}


def test_bin_exchange_rejects_more_than_two_endpoint_groups() -> None:
    invalid = deepcopy(BIN_EXCHANGE)
    invalid["data"]["moves"].extend(
        [
            {
                "container_id": "bin-4",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-3", "rack_face": "90", "slot_id": "3"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-4", "rack_face": "90", "slot_id": "3"},
            },
            {
                "container_id": "bin-5",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-4", "rack_face": "90", "slot_id": "3"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-3", "rack_face": "90", "slot_id": "3"},
            },
        ]
    )

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=invalid)

    assert response.status_code == 422
    assert response.json()["data"]["reason_code"] == "INVALID_DATA"


def test_bin_exchange_rejects_reused_slot_pair() -> None:
    invalid = deepcopy(BIN_EXCHANGE)
    invalid["data"]["moves"].extend(
        [
            {
                "container_id": "bin-4",
                "source": deepcopy(BIN_EXCHANGE["data"]["moves"][0]["source"]),
                "target": deepcopy(BIN_EXCHANGE["data"]["moves"][0]["target"]),
            },
            {
                "container_id": "bin-5",
                "source": deepcopy(BIN_EXCHANGE["data"]["moves"][1]["source"]),
                "target": deepcopy(BIN_EXCHANGE["data"]["moves"][1]["target"]),
            },
        ]
    )

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=invalid)

    assert response.status_code == 422
    assert response.json()["data"]["reason_code"] == "INVALID_DATA"


def test_bin_exchange_accepts_single_endpoint_group() -> None:
    exchange = deepcopy(BIN_EXCHANGE)
    exchange["data"]["moves"][0]["target"] = {
        "kind": "RACK_BIN_SLOT",
        "rack_id": "rack-1",
        "rack_face": "90",
        "slot_id": "3",
    }
    exchange["data"]["moves"][1]["source"] = deepcopy(exchange["data"]["moves"][0]["target"])
    exchange["data"]["moves"][1]["target"] = deepcopy(exchange["data"]["moves"][0]["source"])

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=exchange)

    assert response.status_code == 202
    assert response.json()["code"] == "RECEIVED"


def test_bin_exchange_accepts_two_disjoint_pairs() -> None:
    exchange = deepcopy(BIN_EXCHANGE)
    exchange["data"]["moves"].extend(
        [
            {
                "container_id": "bin-4",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "90", "slot_id": "3"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "90", "slot_id": "3"},
            },
            {
                "container_id": "bin-5",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "90", "slot_id": "3"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "90", "slot_id": "3"},
            },
        ]
    )

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=exchange)

    assert response.status_code == 202
    assert response.json()["code"] == "RECEIVED"


@pytest.mark.parametrize("invalid_topology", ["FOUR_MEMBER_CYCLE", "INTERNAL_EDGE_WITH_TWO_GROUPS"])
def test_bin_exchange_rejects_non_binary_topology(invalid_topology: str) -> None:
    invalid = deepcopy(BIN_EXCHANGE)
    if invalid_topology == "FOUR_MEMBER_CYCLE":
        invalid["data"]["moves"][1]["target"] = {
            "kind": "RACK_BIN_SLOT",
            "rack_id": "rack-1",
            "rack_face": "90",
            "slot_id": "3",
        }
        second_source = deepcopy(invalid["data"]["moves"][1]["target"])
        second_target = {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "90", "slot_id": "3"}
        final_target = deepcopy(invalid["data"]["moves"][0]["source"])
    else:
        second_source = {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "90", "slot_id": "3"}
        second_target = {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "90", "slot_id": "4"}
        final_target = deepcopy(second_source)
    invalid["data"]["moves"].extend(
        [
            {"container_id": "bin-4", "source": second_source, "target": second_target},
            {"container_id": "bin-5", "source": deepcopy(second_target), "target": final_target},
        ]
    )

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=invalid)

    assert response.status_code == 422
    assert response.json()["data"]["reason_code"] == "INVALID_DATA"


def test_bin_exchange_rejects_duplicate_container_id() -> None:
    invalid = deepcopy(BIN_EXCHANGE)
    invalid["data"]["moves"][1]["container_id"] = invalid["data"]["moves"][0]["container_id"]

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=invalid)

    assert response.status_code == 422
    assert response.json()["data"]["reason_code"] == "INVALID_DATA"


def test_transport_submit_replays_duplicate_and_stable_conflict_for_changed_payload(monkeypatch) -> None:
    timestamps = iter(range(1786060801000, 1786060801010))
    monkeypatch.setattr(wms_mock_server, "_now_ms", lambda: next(timestamps))
    changed = deepcopy(RACK_MOVE)
    changed["data"]["target"]["location_code"] = "station-c"
    with TestClient(wms_mock_server.app) as client:
        first = client.post("/api/v1/wes/transport-requests", json=RACK_MOVE)
        replay = client.post(
            "/api/v1/wes/transport-requests",
            content=json.dumps(RACK_MOVE, ensure_ascii=False, indent=2),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        conflict = client.post("/api/v1/wes/transport-requests", json=changed)
        conflict_replay = client.post("/api/v1/wes/transport-requests", json=changed)

    assert replay.status_code == 200
    assert replay.json() == {**first.json(), "code": "DUPLICATE"}
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "CONFLICT"
    assert conflict.json()["data"] == {"transport_task_id": "transport-rack-1"}
    assert conflict_replay.status_code == 409
    assert conflict_replay.json() == conflict.json()


def test_identity_conflict_echoes_the_current_request_transport_task_id() -> None:
    changed = deepcopy(RACK_MOVE)
    changed["data"]["transport_task_id"] = "transport-rack-2"

    with TestClient(wms_mock_server.app) as client:
        first = client.post("/api/v1/wes/transport-requests", json=RACK_MOVE)
        conflict = client.post("/api/v1/wes/transport-requests", json=changed)

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["data"] == {"transport_task_id": "transport-rack-2"}


def test_transport_submit_scopes_identity_by_operation_and_operation_id() -> None:
    unsupported = deepcopy(RACK_MOVE)
    unsupported["operation"] = "transport.task.unsupported@v1"

    with TestClient(wms_mock_server.app) as client:
        rejected = client.post("/api/v1/wes/transport-requests", json=unsupported)
        accepted = client.post("/api/v1/wes/transport-requests", json=RACK_MOVE)

    assert rejected.status_code == 422
    assert rejected.json()["data"]["reason_code"] == "UNSUPPORTED_OPERATION"
    assert accepted.status_code == 202
    assert accepted.json()["code"] == "RECEIVED"


def test_transport_submit_rejects_closed_contract_violation_and_replays_first_rejection() -> None:
    invalid = deepcopy(BIN_MOVE)
    invalid["data"]["moves"][0]["vehicle_id"] = "agv-private"
    with TestClient(wms_mock_server.app) as client:
        first = client.post("/api/v1/wes/transport-requests", json=invalid)
        replay = client.post("/api/v1/wes/transport-requests", json=invalid)

    assert first.status_code == 422
    assert first.json()["code"] == "REJECTED"
    assert first.json()["data"] == {"transport_task_id": "transport-bin-1", "reason_code": "INVALID_DATA"}
    assert replay.status_code == 422
    assert replay.json() == first.json()


@pytest.mark.parametrize("invalid_task_id", [" ", "invalid\x00task", "x" * 81])
def test_rejected_request_omits_invalid_transport_task_id(invalid_task_id: str) -> None:
    invalid = deepcopy(RACK_MOVE)
    invalid["data"]["transport_task_id"] = invalid_task_id

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/v1/wes/transport-requests", json=invalid)

    assert response.status_code == 422
    assert response.json()["data"] == {"reason_code": "INVALID_DATA"}


@pytest.mark.parametrize(
    ("body", "content_type", "expected_status"),
    [
        (b'{"operation_id":', "application/json", 400),
        (
            b'{"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472",'
            b'"operation_id":"019f12d0-58d7-7b4d-a23a-1b90aa5d4472"}',
            "application/json",
            400,
        ),
        (json.dumps(RACK_MOVE).encode(), "text/plain", 400),
        (b"{" + b'"padding":"' + b"x" * (256 * 1024) + b'"}', "application/json", 413),
    ],
)
def test_transport_submit_rejects_preassociation_wire_failures_with_empty_body(
    body: bytes, content_type: str, expected_status: int
) -> None:
    with TestClient(wms_mock_server.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/wes/transport-requests",
            content=body,
            headers={"Content-Type": content_type},
        )

    assert response.status_code == expected_status
    assert response.content == b""


def test_unavailable_fault_is_transient_and_does_not_claim_message_identity() -> None:
    with TestClient(wms_mock_server.app) as client:
        configured = client.post("/debug/transport-submit-mode", json={"mode": "UNAVAILABLE"})
        unavailable = client.post("/api/v1/wes/transport-requests", json=RACK_MOVE)
        accepted = client.post("/api/v1/wes/transport-requests", json=RACK_MOVE)

    assert configured.status_code == 200
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "UNAVAILABLE"
    assert accepted.status_code == 202
    assert accepted.json()["code"] == "RECEIVED"


def test_debug_callback_relay_sends_the_frozen_transport_event_without_authentication(monkeypatch) -> None:
    callback = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["member_target_placed"]["value"])
    captured: list[tuple[str, dict[str, object]]] = []

    async def capture(url: str, payload: dict[str, object]) -> int:
        captured.append((url, payload))
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", capture, raising=False)
    monkeypatch.setattr(
        wms_mock_server,
        "WES_TRANSPORT_EVENT_URL",
        "http://wes.test/api/v1/wms/events",
        raising=False,
    )
    with TestClient(wms_mock_server.app) as client:
        response = client.post("/debug/transport-callbacks", json=callback)

    assert response.status_code == 200
    assert response.json() == {"status_code": 202}
    assert captured == [("http://wes.test/api/v1/wms/events", callback)]


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_determinate_rack_result_releases_resources_and_updates_the_trusted_face(monkeypatch, status: str) -> None:
    callback = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["rack_succeeded"]["value"])
    callback["data"]["transport_task_id"] = RACK_ROTATE["data"]["transport_task_id"]
    callback["data"]["kind"] = "RACK_ROTATE"
    callback["data"]["rack_id"] = RACK_ROTATE["data"]["rack_id"]
    callback["data"]["final_position"] = deepcopy(RACK_ROTATE["data"]["target"])
    callback["data"]["arrival_face"] = RACK_ROTATE["data"]["target_face"]
    callback["data"]["status"] = status
    if status == "FAILED":
        callback["data"]["failure_code"] = "RCS_EXECUTION_FAILED"
    next_rotate = deepcopy(RACK_ROTATE)
    next_rotate["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4493"
    next_rotate["data"]["transport_task_id"] = "transport-rack-next"
    next_rotate["data"]["target_face"] = "90"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        first = client.post("/api/v1/wes/transport-requests", json=RACK_ROTATE)
        callback_response = client.post("/debug/transport-callbacks", json=callback)
        next_response = client.post("/api/v1/wes/transport-requests", json=next_rotate)

    assert first.status_code == 202
    assert callback_response.status_code == 200
    assert next_response.status_code == 202


def test_determinate_bin_result_releases_container_and_rack_resources(monkeypatch) -> None:
    callback = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["bin_succeeded"]["value"])
    next_move = deepcopy(BIN_MOVE)
    next_move["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4495"
    next_move["data"]["transport_task_id"] = "transport-bin-next"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        first = client.post("/api/v1/wes/transport-requests", json=BIN_MOVE)
        callback_response = client.post("/debug/transport-callbacks", json=callback)
        next_response = client.post("/api/v1/wes/transport-requests", json=next_move)

    assert first.status_code == 202
    assert callback_response.status_code == 200
    assert next_response.status_code == 202


def test_position_unknown_result_keeps_the_active_resource_binding(monkeypatch) -> None:
    callback = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["rack_succeeded"]["value"])
    callback["data"] = {
        "transport_task_id": RACK_MOVE["data"]["transport_task_id"],
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": RACK_MOVE["data"]["rack_id"],
        "status": "FAILED",
        "position_unknown": True,
        "failure_code": "POSITION_UNKNOWN",
    }
    conflicting = deepcopy(RACK_MOVE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4494"
    conflicting["data"]["transport_task_id"] = "transport-rack-unknown-conflict"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 202
        assert client.post("/debug/transport-callbacks", json=callback).status_code == 200
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert conflict.status_code == 409


def test_callback_ack_does_not_release_resources_for_a_different_frozen_kind(monkeypatch) -> None:
    callback = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["bin_succeeded"]["value"])
    callback["data"]["transport_task_id"] = RACK_MOVE["data"]["transport_task_id"]
    conflicting = deepcopy(RACK_MOVE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4496"
    conflicting["data"]["transport_task_id"] = "transport-rack-kind-conflict"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 202
        assert client.post("/debug/transport-callbacks", json=callback).status_code == 200
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert conflict.status_code == 409


def test_callback_ack_does_not_release_resources_for_a_mismatched_success_target(monkeypatch) -> None:
    callback = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["rack_succeeded"]["value"])
    callback["data"]["final_position"]["location_code"] = "unexpected-position"
    conflicting = deepcopy(RACK_MOVE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4497"
    conflicting["data"]["transport_task_id"] = "transport-rack-target-conflict"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 202
        assert client.post("/debug/transport-callbacks", json=callback).status_code == 200
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert conflict.status_code == 409


def test_lower_outcome_revision_cannot_release_resources_after_position_unknown(monkeypatch) -> None:
    unknown = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["rack_succeeded"]["value"])
    unknown["data"] = {
        "transport_task_id": RACK_MOVE["data"]["transport_task_id"],
        "kind": "RACK_MOVE",
        "outcome_revision": 2,
        "rack_id": RACK_MOVE["data"]["rack_id"],
        "status": "FAILED",
        "position_unknown": True,
        "failure_code": "POSITION_UNKNOWN",
    }
    stale_success = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["rack_succeeded"]["value"])
    stale_success["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4498"
    conflicting = deepcopy(RACK_MOVE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4499"
    conflicting["data"]["transport_task_id"] = "transport-rack-revision-conflict"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=RACK_MOVE).status_code == 202
        assert client.post("/debug/transport-callbacks", json=unknown).status_code == 200
        assert client.post("/debug/transport-callbacks", json=stale_success).status_code == 200
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert conflict.status_code == 409


def test_higher_revision_cannot_rewrite_a_known_member_from_partial_unknown(monkeypatch) -> None:
    first_result = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["bin_succeeded"]["value"])
    first_result["data"] = {
        "transport_task_id": BIN_EXCHANGE["data"]["transport_task_id"],
        "kind": "BIN_EXCHANGE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-2",
                "status": "FAILED",
                "final_position": deepcopy(BIN_EXCHANGE["data"]["moves"][0]["source"]),
                "failure_code": "RCS_EXECUTION_FAILED",
            },
            {
                "container_id": "bin-3",
                "status": "FAILED",
                "position_unknown": True,
                "failure_code": "POSITION_UNKNOWN",
            },
        ],
    }
    changed_result = deepcopy(first_result)
    changed_result["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4500"
    changed_result["data"]["outcome_revision"] = 2
    changed_result["data"]["results"] = [
        {
            "container_id": "bin-2",
            "status": "FAILED",
            "final_position": deepcopy(BIN_EXCHANGE["data"]["moves"][0]["target"]),
            "failure_code": "RCS_EXECUTION_FAILED",
        },
        {
            "container_id": "bin-3",
            "status": "SUCCEEDED",
            "final_position": deepcopy(BIN_EXCHANGE["data"]["moves"][1]["target"]),
        },
    ]
    conflicting = deepcopy(BIN_EXCHANGE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4501"
    conflicting["data"]["transport_task_id"] = "transport-bin-known-fact-conflict"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=BIN_EXCHANGE).status_code == 202
        assert client.post("/debug/transport-callbacks", json=first_result).status_code == 200
        assert client.post("/debug/transport-callbacks", json=changed_result).status_code == 200
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert conflict.status_code == 409


def test_transport_final_result_cannot_rewrite_a_position_confirmed_by_target_placed(monkeypatch) -> None:
    target_placed = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["member_target_placed"]["value"])
    contradictory_result = deepcopy(wms_transport_mock_openapi.TRANSPORT_CALLBACK_EXAMPLES["bin_succeeded"]["value"])
    contradictory_result["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4502"
    contradictory_result["data"]["results"] = [
        {
            "container_id": "bin-1",
            "status": "FAILED",
            "final_position": deepcopy(BIN_MOVE["data"]["moves"][0]["source"]),
            "failure_code": "RCS_EXECUTION_FAILED",
        }
    ]
    conflicting = deepcopy(BIN_MOVE)
    conflicting["operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4503"
    conflicting["data"]["transport_task_id"] = "transport-bin-position-conflict"

    async def accepted(_: str, __: dict[str, object]) -> int:
        return 202

    monkeypatch.setattr(wms_mock_server, "_post_transport_callback", accepted)
    with TestClient(wms_mock_server.app) as client:
        assert client.post("/api/v1/wes/transport-requests", json=BIN_MOVE).status_code == 202
        assert client.post("/debug/transport-callbacks", json=target_placed).status_code == 200
        assert client.post("/debug/transport-callbacks", json=contradictory_result).status_code == 200
        conflict = client.post("/api/v1/wes/transport-requests", json=conflicting)

    assert conflict.status_code == 409
