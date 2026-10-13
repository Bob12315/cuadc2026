from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.mission_service import MissionApplicationService
from missions.common.actions.select_drop_targets import SelectDropTargetsAction
from missions.engine import MissionActionStep, MissionOrchestrator


TEMPLATE = json.loads(
    (Path(__file__).resolve().parents[3] / "config/action_missions/rescue_2026_full_auto.json").read_text()
)
SELECT_PARAMS = next(step["params"] for step in TEMPLATE["steps"] if step["name"] == "select_drop_targets")


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_full_mission_drop_plan_for_visible_target_count(count: int) -> None:
    objects = [
        {
            "id": f"bucket_{index}", "target_id": f"bucket_{index}",
            "class_name": f"bucket_{index}", "lat": 34.0 + index * 0.0001,
            "lon": 108.0 + index * 0.0001, "east_m": index * 3.0,
            "north_m": index * 3.0, "seen_count": 1, "raw_count": 1,
        }
        for index in range(1, count + 1)
    ]
    action = SelectDropTargetsAction()
    action.start({**SELECT_PARAMS, "objects": objects})
    result = action.update({})

    assert result.done and not result.failed
    assert result.output["selected_count"] == min(count, 2)
    assert len(result.output["target_slots"]) == 2
    assert [output["channel"] for output in result.output["first_release_servo_outputs"]] == (
        [8] if count >= 2 else [8, 9]
    )
    assert result.output["first_alignment_enabled"] is (count > 0)
    assert result.output["second_alignment_enabled"] is (count >= 2)
    assert result.output["second_release_enabled"] is (count >= 2)
    if count == 0:
        assert result.output["target_slots"][0]["status"] == "fallback_center"
        assert result.output["target_slots"][1]["valid"] is False
    if count == 3:
        assert [target["class_name"] for target in result.output["selected_targets"]] == [
            "bucket_1", "bucket_2",
        ]


class _Runtime:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.ticked: list[str] = []
        self.last_result: dict[str, object] = {}
        self.command_port = SimpleNamespace(cancel_run_commands=self.cancel_run)
        self.cancelled_runs: list[str] = []
        self.stopped_continuous: list[tuple[str, bool]] = []
        self.dispatcher = SimpleNamespace(
            authorization=SimpleNamespace(run_id="run-1"),
            command_port=self.command_port,
            safety_pipeline=SimpleNamespace(stop_continuous=self.stop_continuous),
            clear_authorization=lambda reason: None,
        )

    def start(self, name, params, **kwargs):
        self.started.append(name)
        self.last_result = {"done": False, "failed": False, "reason": "started"}

    def tick(self, context, **kwargs):
        name = self.started[-1]
        self.ticked.append(name)
        self.last_result = {"done": name == "goto_waypoint", "failed": False, "reason": "updated"}
        return {}

    def clear_navigation_queue(self, *args, **kwargs):
        pass

    def resume_after_pause(self, duration_s):
        self.resumed_after_s = duration_s

    def cancel_run(self, run_id):
        self.cancelled_runs.append(run_id)

    def stop_continuous(self, reason, *, emit):
        self.stopped_continuous.append((reason, emit))


def test_non_guided_pauses_before_land_and_guided_resumes_same_step() -> None:
    mode = {"value": "GUIDED"}
    runtime = _Runtime()
    host = SimpleNamespace(
        action_runtime_lock=threading.RLock(), action_runtime=runtime,
        action_lab_context=lambda: {"drone": {"mode": mode["value"]}},
        controller_switches=SimpleNamespace(snapshot=lambda: SimpleNamespace(send_commands=True)),
        result_service=SimpleNamespace(set=lambda *args: None),
        logger=logging.getLogger(__name__),
        _maybe_save_localization_from_mission=lambda: None,
        _maybe_save_localization_result=lambda: None,
        _maybe_save_drop_targets_result=lambda: None,
        _save_drop_workflow_from_action_result=lambda *args, **kwargs: None,
    )
    service = MissionApplicationService(host)
    service.action_mission_orchestrator = MissionOrchestrator(
        runtime, [MissionActionStep("goto_waypoint"), MissionActionStep("land")]
    )
    service.action_mission_orchestrator.start()
    service.action_mission_tick()
    assert runtime.started == ["goto_waypoint", "land"]

    mode["value"] = "STABILIZE"
    paused = service.action_mission_tick()
    assert paused.paused and paused.running and paused.current_index == 1
    assert paused.reason == "flight_mode_not_guided"
    assert runtime.ticked == ["goto_waypoint"]
    assert runtime.cancelled_runs == ["run-1"]
    assert runtime.stopped_continuous == [("flight_mode_not_guided", False)]
    service.action_mission_tick()
    assert runtime.cancelled_runs == ["run-1"]
    assert runtime.ticked == ["goto_waypoint"]

    mode["value"] = "GUIDED"
    resumed = service.action_mission_tick()
    assert not resumed.paused and resumed.current_index == 1
    assert runtime.ticked == ["goto_waypoint", "land"]
    assert runtime.resumed_after_s >= 0.0


def test_starting_in_manual_does_not_advance_takeoff_or_switch_mode() -> None:
    runtime = _Runtime()
    host = SimpleNamespace(
        action_runtime_lock=threading.RLock(), action_runtime=runtime,
        action_lab_context=lambda: {"drone": {"mode": "STABILIZE"}},
        logger=logging.getLogger(__name__),
    )
    service = MissionApplicationService(host)
    service.action_mission_orchestrator = MissionOrchestrator(runtime, [MissionActionStep("takeoff")])
    service.action_mission_orchestrator.start()

    status = service.action_mission_tick()

    assert status.running and status.paused
    assert runtime.started == ["takeoff"]
    assert runtime.ticked == []
    assert runtime.cancelled_runs == ["run-1"]
