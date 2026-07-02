"""Phase 3 ScenarioReplayRunner resilience fixture replay."""

from __future__ import annotations

import json
from pathlib import Path

from src.app.runtime.orchestration.scenario_replay import (
    ScenarioEvent,
    ScenarioRecording,
    ScenarioReplayRunner,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "phase3_runtime_replay_fixture.json"


def _load_recording() -> ScenarioRecording:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return ScenarioRecording(
        scenario_id=raw["scenario_id"],
        schema_version=raw["schema_version"],
        events=tuple(ScenarioEvent(**event) for event in raw["events"]),
    )


def test_phase3_resilience_fixture_replays_deterministically() -> None:
    recording = _load_recording()
    runner = ScenarioReplayRunner()

    first = runner.replay(recording)
    second = runner.replay(recording)

    assert first == second
    assert first.timeline == (
        "device_command:evt-001",
        "callback_out_of_order:evt-002",
        "wms_reject:evt-003",
    )
    assert first.outbox_effect_keys == ("device-command:CMD-REPLAY-001", "wms-fulfillment:FUL-REPLAY-001")
    assert first.reconciliation_reasons == ("late_device_result", "wms_business_reject")
    assert first.projection_hash == "517bf5710bafdb1ecba9d61c430450c019284edffe7f9afed32bc3d270347d4d"
