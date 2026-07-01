"""Phase 3 PlaneSceneView / PlaneSnapshot schema contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_plane_scene_and_snapshot_have_independent_schema_versions() -> None:
    """scene 与 snapshot 必须独立 version, 前端不能混用。"""

    from src.app.workline.models.plane import PlaneSceneView, PlaneSnapshot

    scene = PlaneSceneView(
        schema_version="plane.scene.v1",
        workline_code="WL-1",
        nodes=[{"code": "SCAN1", "label": "扫码位 1", "kind": "station"}],
        edges=[],
    )
    snapshot = PlaneSnapshot(
        schema_version="plane.snapshot.v1",
        workline_code="WL-1",
        scene_schema_version="plane.scene.v1",
        objects=[{"object_code": "BIN-1", "object_label": "料箱 1", "state": "RUNNING"}],
        extremes=[],
    )

    assert scene.schema_version == "plane.scene.v1"
    assert snapshot.schema_version == "plane.snapshot.v1"
    assert snapshot.scene_schema_version == scene.schema_version


def test_plane_read_model_rejects_label_without_code() -> None:
    """code/label 分离: 可展示 label, 但稳定 code 必填。"""

    from src.app.workline.models.plane import PlaneSceneView

    with pytest.raises(ValidationError):
        PlaneSceneView(
            schema_version="plane.scene.v1",
            workline_code="WL-1",
            nodes=[{"label": "扫码位 1", "kind": "station"}],
            edges=[],
        )
