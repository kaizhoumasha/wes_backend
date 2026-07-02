"""Phase 3 ScenarioReplayRunner resilience fixture replay."""

from __future__ import annotations

import json
from pathlib import Path

from src.app.runtime.orchestration.scenario_replay import (
    ScenarioEvent,
    ScenarioRecorder,
    ScenarioRecording,
    ScenarioReplayRunner,
)

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "phase3_runtime_replay_fixture.json"
SIMULATOR_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "phase3_simulator_replay_fixture.json"


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


def test_phase3_simulator_fixture_records_and_replays_deterministically() -> None:
    raw = json.loads(SIMULATOR_FIXTURE_PATH.read_text(encoding="utf-8"))

    recording = ScenarioRecorder().record_simulator_events(
        scenario_id=raw["scenario_id"],
        simulator_events=raw["simulator_events"],
    )
    result = ScenarioReplayRunner().replay(recording)

    assert recording.schema_version == "scenario.v1"
    assert recording.events[0].payload["pkg_code"] == "***0001"
    assert recording.events[0].payload["bin_code"] == "BIN***"
    assert recording.events[0].payload["source_system"] == "ECS"
    assert result.timeline == (
        "device_command:sim-ecs-001",
        "callback_out_of_order:sim-ecs-002",
        "wms_reject:sim-wms-001",
    )
    assert result.reconciliation_reasons == ("late_device_result", "wms_business_reject")
