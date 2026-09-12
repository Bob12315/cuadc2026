from __future__ import annotations

from application.action_runtime import ActionRuntimeService
from execution.dispatcher import ActionDispatcher
from missions.common.actions.action_lab import create_action_lab_registry
from missions.common.actions.base import ActionModule
from missions.common.actions.goto_waypoint import GotoWaypointAction
from missions.common.actions.registry import ActionRegistry
from missions.common.actions.result import ActionResult
from missions.common.actions.runner import ActionRunner


def _context(
    *,
    now: float = 0.0,
    lat: float = 34.0,
    lon: float = 108.0,
    altitude_m: float = 3.0,
    horizontal_speed_mps: float = 0.05,
    vertical_speed_mps: float = 0.02,
    mode: str = "GUIDED",
    control_allowed: bool = True,
) -> dict:
    return {
        "now_monotonic": now,
        "field_heading_confirmed": True,
        "field_origin_gps_confirmed": True,
        "field_heading_yaw_rad": 0.0,
        "field_origin_lat": 34.0,
        "field_origin_lon": 108.0,
        "field_reference": {
            "is_confirmed": True,
            "synced_to_runtime": True,
            "is_frozen": True,
            "is_ready_for_field_to_gps": True,
        },
        "drone": {
            "global_position_valid": True,
            "attitude_valid": True,
            "yaw": 0.0,
            "lat": lat,
            "lon": lon,
            "relative_altitude": altitude_m,
            "velocity_valid": True,
            "vx": horizontal_speed_mps,
            "vy": 0.0,
            "vz": vertical_speed_mps,
            "mode": mode,
            "control_allowed": control_allowed,
        },
    }


def _params(**overrides: object) -> dict:
    return {
        "field_x_m": 0.0,
        "field_y_m": 0.0,
        "altitude_m": 3.0,
        "tolerance_xy_m": 0.30,
        "tolerance_z_m": 0.30,
        "min_hold_updates": 4,
        "require_velocity_valid": True,
        "max_horizontal_speed_mps": 0.25,
        "max_vertical_speed_mps": 0.15,
        "position_hysteresis_xy_m": 0.20,
        "position_hysteresis_z_m": 0.10,
        "horizontal_speed_hysteresis_mps": 0.10,
        "vertical_speed_hysteresis_mps": 0.05,
        "control_reject_max_updates": 3,
        "control_reject_timeout_s": 1.0,
        "max_duration_s": 180.0,
    } | overrides


def _runtime() -> ActionRuntimeService:
    return ActionRuntimeService(
        runner=ActionRunner(create_action_lab_registry()),
        dispatcher=ActionDispatcher(test_source="test"),
    )


def test_goto_reaches_target_and_releases_active_action() -> None:
    service = _runtime()
    service.start("goto_waypoint", _params())

    for index in range(4):
        status = service.tick(
            _context(now=index * 0.5), link_manager=None, send_commands=False
        )

    assert service.last_result is not None
    assert service.last_result["reason"] == "waypoint_reached"
    assert status["state"] == "succeeded"
    assert status["action_name"] is None
    assert status["action_id"] is None
    assert status["active_target"] is None
    assert status["last_terminal"]["state"] == "succeeded"


def test_goto_small_speed_spike_decays_instead_of_resetting_reach_progress() -> None:
    action = GotoWaypointAction()
    action.start(_params())

    for index in range(3):
        result = action.update(_context(now=index * 0.5))
        assert result.detail["reached_updates"] == index + 1

    spike = action.update(
        _context(now=1.5, horizontal_speed_mps=0.30, vertical_speed_mps=0.16)
    )
    assert spike.detail["reached_update_action"] == "decrement"
    assert spike.detail["reached_updates"] == 2
    assert spike.detail["position_gate_passed"] is True
    assert spike.detail["velocity_gate_passed"] is False

    assert not action.update(_context(now=2.0)).done
    final = action.update(_context(now=2.5))
    assert final.done is True
    assert final.reason == "waypoint_reached"


def test_goto_clearly_leaving_target_resets_reach_progress() -> None:
    action = GotoWaypointAction()
    action.start(_params())
    action.update(_context(now=0.0))
    action.update(_context(now=0.5))

    departed = action.update(_context(now=1.0, lat=34.001))

    assert departed.detail["reached_update_action"] == "reset"
    assert departed.detail["reached_updates"] == 0


def test_goto_guided_loss_fails_and_releases_active_action() -> None:
    service = _runtime()
    service.start("goto_waypoint", _params())
    service.dispatcher.dispatched_keys.add("old-goto-key")

    for now in (0.0, 0.5, 1.0):
        status = service.tick(
            _context(
                now=now,
                mode="LOITER",
                control_allowed=False,
            ),
            link_manager=None,
            send_commands=False,
        )

    assert service.last_result is not None
    assert service.last_result["failed"] is True
    assert service.last_result["reason"] == "control_not_allowed"
    assert status["state"] == "failed"
    assert status["action_name"] is None
    assert status["last_terminal"]["state"] == "failed"
    assert service.dispatcher.dispatched_keys == set()


def test_dispatcher_control_rejections_cannot_leave_goto_running() -> None:
    service = _runtime()
    service.start("goto_waypoint", _params(control_reject_max_updates=3))
    service.dispatcher.dispatch_result = lambda *args, **kwargs: {
        "accepted": [],
        "sent": [],
        "errors": [],
        "skipped": [{"reason": "control_not_allowed"}],
    }
    context = _context()
    context["drone"].pop("mode")
    context["drone"].pop("control_allowed")

    for index in range(3):
        status = service.tick(
            context | {"now_monotonic": index * 0.5},
            link_manager=None,
            send_commands=True,
        )
        assert status["state"] == "running"

    status = service.tick(
        context | {"now_monotonic": 1.5},
        link_manager=None,
        send_commands=True,
    )

    assert service.last_result is not None
    assert service.last_result["reason"] == "control_not_allowed"
    assert status["state"] == "failed"
    assert status["action_name"] is None


def test_goto_timeout_cleans_up_when_position_telemetry_never_arrives() -> None:
    service = _runtime()
    service.start("goto_waypoint", _params(max_duration_s=1.0))
    waiting = _context(now=0.0)
    waiting["drone"]["global_position_valid"] = False

    first = service.tick(waiting, link_manager=None, send_commands=False)
    final = service.tick(
        waiting | {"now_monotonic": 1.0},
        link_manager=None,
        send_commands=False,
    )

    assert first["state"] == "running"
    assert service.last_result is not None
    assert service.last_result["reason"] == "goto_timeout"
    assert final["state"] == "failed"
    assert final["action_name"] is None


def test_new_goto_preempts_running_goto_and_uses_new_target() -> None:
    service = _runtime()
    old_start = service.start("goto_waypoint", _params(field_y_m=10.0))

    new_start = service.start("goto_waypoint", _params(field_y_m=0.0))

    assert new_start.failed is False
    preempted = new_start.detail["preempted_action"]
    assert preempted["old_action_id"] == old_start.detail["action_id"]
    assert preempted["old_target"] == {
        "field_x_m": 0.0,
        "field_y_m": 10.0,
        "alt_m": 3.0,
    }
    assert preempted["new_target"]["field_y_m"] == 0.0
    assert preempted["preempt_reason"] == "superseded_by_new_goto"
    assert service.runner.state == "running"
    assert service.runner.action_name == "goto_waypoint"
    assert service.runner.active_target == {
        "field_x_m": 0.0,
        "field_y_m": 0.0,
        "alt_m": 3.0,
    }


def test_runner_cleans_active_registry_for_succeeded_failed_and_cancelled() -> None:
    events: list[str] = []

    class TerminalAction(ActionModule):
        def start(self, params=None):
            self.outcome = (params or {})["outcome"]

        def update(self, context=None):
            if self.outcome == "succeeded":
                return ActionResult(done=True, reason="done")
            return ActionResult(failed=True, reason="failed")

        def stop(self):
            events.append("stop")

        def reset(self):
            events.append("reset")

    registry = ActionRegistry()
    registry.register("terminal", TerminalAction)

    for expected_state in ("succeeded", "failed", "cancelled"):
        runner = ActionRunner(registry)
        runner.start("terminal", {"outcome": expected_state})
        if expected_state == "cancelled":
            result = runner.stop()
        else:
            result = runner.update()

        assert runner.state == expected_state
        assert runner.current_action is None
        assert runner.action_name is None
        assert runner.action_id is None
        assert runner.active_target is None
        assert result.detail["lifecycle_state"] == expected_state
        assert runner.status()["last_terminal"]["state"] == expected_state

    assert events.count("reset") == 3
