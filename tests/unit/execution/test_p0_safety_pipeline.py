from __future__ import annotations

import time
from types import SimpleNamespace

from execution.authorization import RunAuthorization
from execution.safety_config import load_safety_config
from execution.safety_pipeline import ActionSafetyPipeline


def _authorization(action: str = "align_descend", source: str = "sitl") -> RunAuthorization:
    return RunAuthorization.create(
        operator="p0-test",
        scope_type="action",
        scope_name=action,
        target_source=source,
        allowed_actions={action},
    )


def _request(action_type: str, params: dict[str, object], *, age_s: float = 0.0) -> dict[str, object]:
    return {
        "action_type": action_type,
        "params": params,
        "key": "p0-test-key",
        "generated_at_monotonic": time.monotonic() - age_s,
    }


class _LiveLink:
    def __init__(self, **state: object) -> None:
        defaults = {
            "connected": True,
            "stale": False,
            "control_allowed": True,
            "mode": "GUIDED",
            "armed": True,
            "local_position_valid": True,
            "local_x": 0.0,
            "local_y": 0.0,
            "local_z": -2.0,
            "global_position_valid": True,
            "lat": 34.0,
            "lon": 108.0,
        }
        defaults.update(state)
        self.state = SimpleNamespace(**defaults)
        self.source = "sitl"
        self.stops = 0
        self.nav_clears = 0

    def get_active_source(self) -> str:
        return self.source

    def get_latest_drone_state(self):
        return self.state

    def stop_body_velocity_and_clear(self) -> None:
        self.stops += 1

    def clear_pending_local_position_actions(self) -> None:
        self.nav_clears += 1


def test_continuous_request_is_clamped_and_preserves_original() -> None:
    pipeline = ActionSafetyPipeline(allow_test_source=True)
    request = _request(
        "flight_command",
        {"valid": True, "active": True, "vx_cmd": 9.0, "vy_cmd": -8.0, "vz_cmd": 7.0},
    )
    decision = pipeline.evaluate(
        request,
        action_name="align_descend",
        source="sitl",
        authorization=_authorization(),
        link_manager=_LiveLink(),
    )

    assert decision.status == "clamped"
    assert decision.original_request["params"]["vx_cmd"] == 9.0
    assert decision.effective_request is not None
    assert decision.effective_request["params"] == {
        "valid": True,
        "active": True,
        "vx_cmd": 0.4,
        "vy_cmd": -0.4,
        "vz_cmd": 0.35,
    }


def test_pipeline_rejects_expired_stale_and_wrong_source_requests() -> None:
    pipeline = ActionSafetyPipeline(allow_test_source=True)
    request = _request("land", {}, age_s=1.0)
    expired = pipeline.evaluate(
        request,
        action_name="land",
        source="sitl",
        authorization=_authorization("land"),
        link_manager=_LiveLink(),
    )
    assert expired.reason_code == "request_expired"

    stale = pipeline.evaluate(
        _request("land", {}),
        action_name="land",
        source="sitl",
        authorization=_authorization("land"),
        link_manager=_LiveLink(stale=True),
    )
    assert stale.reason_code == "telemetry_stale"

    wrong_source = pipeline.evaluate(
        _request("land", {}),
        action_name="land",
        source="real",
        authorization=_authorization("land", "sitl"),
        link_manager=_LiveLink(),
    )
    assert wrong_source.reason_code == "run_source_mismatch"


def test_navigation_takeoff_and_payload_envelopes_fail_closed() -> None:
    pipeline = ActionSafetyPipeline(allow_test_source=True)

    far = pipeline.evaluate(
        _request("global_goto", {"lat": 34.01, "lon": 108.0, "alt": 3.0, "frame": 6}),
        action_name="goto_waypoint",
        source="sitl",
        authorization=_authorization("goto_waypoint"),
        link_manager=_LiveLink(),
    )
    assert far.reason_code == "global_waypoint_distance_exceeded"

    takeoff = pipeline.evaluate(
        _request("takeoff", {"altitude_m": 3.0}),
        action_name="takeoff",
        source="sitl",
        authorization=_authorization("takeoff"),
        link_manager=_LiveLink(armed=False),
    )
    assert takeoff.reason_code == "takeoff_requires_armed"

    servo = pipeline.evaluate(
        _request("set_servo", {"channel": 9, "pwm": 1000}),
        action_name="payload_release",
        source="sitl",
        authorization=_authorization("payload_release"),
        link_manager=_LiveLink(),
    )
    assert servo.reason_code == "servo_pwm_out_of_range"


def test_sitl_payload_outputs_match_the_gazebo_release_bridge() -> None:
    config = load_safety_config()
    assert [(item.servo_output, item.min_pwm, item.max_pwm) for item in config.servo_outputs] == [
        (8, 1370, 1800),
        (9, 1325, 1745),
    ]


def test_schema_v3_rejects_field_to_local_target_unconditionally() -> None:
    pipeline = ActionSafetyPipeline(allow_test_source=True)
    request = _request("local_position", {"x": 1.0, "y": 2.0, "z": -3.0, "frame": 1})
    request["input_frame"] = "field"
    request["field_reference_confirmed"] = True
    request["field_reference_synced"] = False
    request["field_reference_frozen"] = True

    decision = pipeline.evaluate(
        request,
        action_name="goto_waypoint",
        source="sitl",
        authorization=_authorization("goto_waypoint"),
        link_manager=_LiveLink(),
    )
    assert decision.reason_code == "field_local_target_not_supported"


def test_independent_deadman_emits_explicit_stop() -> None:
    pipeline = ActionSafetyPipeline(allow_test_source=True)
    link = _LiveLink()
    authorization = _authorization()
    request = _request(
        "flight_command",
        {"valid": True, "active": True, "vx_cmd": 0.2, "vy_cmd": 0.0, "vz_cmd": 0.0},
    )
    pipeline.arm_continuous(
        request=request,
        action_name="align_descend",
        source="sitl",
        authorization=authorization,
        state_port=link,
        command_port=link,
    )

    deadline = time.monotonic() + 1.5
    while link.stops == 0 and time.monotonic() < deadline:
        time.sleep(0.02)

    assert link.stops == 1
    assert link.nav_clears == 1
    pipeline.continuous_guard.close()
