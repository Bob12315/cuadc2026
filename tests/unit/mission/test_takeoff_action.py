from __future__ import annotations

import math

import pytest

from missions.common.actions.takeoff import TakeoffAction


FIELD_HEADING = {
    "field_heading_confirmed": True,
    "field_heading_yaw_rad": math.pi / 4,
}
GUIDED = FIELD_HEADING | {"drone": {"mode": "GUIDED", "armed": False}}
GUIDED_ARMED = FIELD_HEADING | {"drone": {"mode": "GUIDED", "armed": True}}


def _advance_to_wait(action: TakeoffAction) -> None:
    action.update(FIELD_HEADING)
    action.update(GUIDED)
    action.update(GUIDED)
    action.update(GUIDED_ARMED)
    action.update(GUIDED_ARMED)


def test_takeoff_start_uses_default_params() -> None:
    action = TakeoffAction()
    action.start({})
    assert action.altitude_m == 3.0
    assert action.mode == "GUIDED"
    assert action.yaw_mode == "field_heading"
    assert action.phase == "set_mode"


def test_takeoff_update_before_start_fails() -> None:
    action = TakeoffAction()
    result = action.update({})
    assert result.failed is True
    assert result.reason == "action_not_started"


def test_takeoff_set_mode_phase_outputs_set_mode_action() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_mode": "hold"})
    result = action.update({})
    assert result.reason == "set_mode_sent"
    assert result.actions[0]["action_type"] == "set_mode"
    assert result.actions[0]["params"]["mode"] == "GUIDED"
    assert result.actions[0]["once"] is True


def test_takeoff_arm_phase_outputs_arm_action() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_mode": "hold"})
    action.update({})
    action.update(GUIDED)
    result = action.update(GUIDED)
    assert result.reason == "arm_sent"
    assert result.actions[0]["action_type"] == "arm"


def test_takeoff_phase_outputs_takeoff_action() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_mode": "hold"})
    action.update({})
    action.update(GUIDED)
    action.update(GUIDED)
    action.update(GUIDED_ARMED)
    result = action.update(GUIDED_ARMED)
    assert result.reason == "takeoff_sent"
    assert result.actions[0]["action_type"] == "takeoff"
    assert result.actions[0]["params"]["altitude_m"] == 3.0


def test_takeoff_wait_altitude_until_target_reached() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_mode": "hold"})
    _advance_to_wait(action)
    waiting = action.update({"relative_altitude": 1.0})
    reached = action.update({"relative_altitude": 2.8})
    assert waiting.done is False
    assert waiting.reason == "waiting_for_takeoff_altitude"
    assert reached.done is True
    assert reached.reason == "takeoff_altitude_reached"


def test_takeoff_reads_altitude_from_local_position_z() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_mode": "hold"})
    _advance_to_wait(action)
    result = action.update({"local_position": {"x": 0, "y": 0, "z": -2.9}})
    assert result.done is True
    assert result.reason == "takeoff_altitude_reached"
    assert result.detail["current_altitude_m"] == 2.9
    assert result.detail["altitude_source"] == "local_position.z"


def test_takeoff_skips_arm_when_require_armed_false() -> None:
    action = TakeoffAction()
    action.start({"require_armed": False, "yaw_mode": "hold"})
    set_mode = action.update({})
    action.update(GUIDED)
    takeoff = action.update(GUIDED)
    assert set_mode.reason == "set_mode_sent"
    assert takeoff.reason == "takeoff_sent"
    assert takeoff.actions[0]["action_type"] == "takeoff"
    assert action.arm_sent is False


def test_takeoff_waits_for_altitude_data_without_immediate_failure() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_mode": "hold"})
    _advance_to_wait(action)
    result = action.update({})
    assert result.failed is False
    assert result.done is False
    assert result.reason == "waiting_for_altitude"


def test_takeoff_times_out_after_max_updates() -> None:
    action = TakeoffAction()
    action.start({"max_updates": 3, "yaw_mode": "hold"})
    action.update({})
    action.update(GUIDED)
    action.update(GUIDED)
    result = action.update({"relative_altitude": 0.2})
    assert result.failed is True
    assert result.reason == "takeoff_timeout"


def test_takeoff_duration_starts_after_takeoff_effect(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("missions.common.actions.takeoff.time.monotonic", lambda: clock[0])
    action = TakeoffAction()
    action.start({"max_updates": 20, "max_duration_s": 1.0, "yaw_mode": "hold"})
    action.update({})
    clock[0] = 5.0
    action.update(GUIDED)
    clock[0] = 10.0
    action.update(GUIDED)
    clock[0] = 15.0
    action.update(GUIDED_ARMED)
    clock[0] = 20.0
    sent = action.update(GUIDED_ARMED)
    assert sent.reason == "takeoff_sent"
    clock[0] = 20.5
    assert action.update({"relative_altitude": 0.0}).reason == "waiting_for_takeoff_altitude"
    clock[0] = 21.1
    assert action.update({"relative_altitude": 0.0}).reason == "takeoff_timeout"


def test_takeoff_rejects_invalid_altitude() -> None:
    action = TakeoffAction()
    with pytest.raises(ValueError):
        action.start({"altitude_m": 0})


def test_takeoff_rejects_empty_mode() -> None:
    action = TakeoffAction()
    with pytest.raises(ValueError):
        action.start({"mode": " "})


def test_takeoff_fails_before_arming_when_field_heading_is_unavailable() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0})

    result = action.update({})

    assert result.failed is True
    assert result.reason == "field_heading_not_ready"
    assert result.actions == []


def test_takeoff_aligns_to_field_positive_y_before_completing() -> None:
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_min_hold_updates": 2})
    _advance_to_wait(action)
    below_target = action.update(FIELD_HEADING | {
        "relative_altitude": 1.0,
        "drone": {"attitude_valid": True, "yaw": 0.0},
    })
    command = action.update(FIELD_HEADING | {
        "relative_altitude": 2.8,
        "drone": {"attitude_valid": True, "yaw": 0.0},
    })
    first_reached = action.update({
        "drone": {"attitude_valid": True, "yaw": math.pi / 4},
    })
    complete = action.update({
        "drone": {"attitude_valid": True, "yaw": math.pi / 4},
    })

    assert below_target.reason == "waiting_for_takeoff_altitude"
    assert command.reason == "field_heading_yaw_sent"
    assert command.actions[0]["action_type"] == "condition_yaw"
    assert command.actions[0]["params"]["yaw_deg"] == pytest.approx(45.0)
    assert command.actions[0]["params"]["relative"] is False
    assert first_reached.reason == "waiting_for_field_heading_yaw"
    assert complete.done is True
    assert complete.reason == "takeoff_field_heading_reached"


def test_takeoff_yaw_alignment_times_out(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("missions.common.actions.takeoff.time.monotonic", lambda: clock[0])
    action = TakeoffAction()
    action.start({"altitude_m": 3.0, "yaw_timeout_s": 1.0})
    _advance_to_wait(action)
    sent = action.update(FIELD_HEADING | {
        "relative_altitude": 2.8,
        "drone": {"attitude_valid": True, "yaw": 0.0},
    })
    clock[0] = 1.0
    timed_out = action.update({"drone": {"attitude_valid": True, "yaw": 0.0}})

    assert sent.reason == "field_heading_yaw_sent"
    assert timed_out.failed is True
    assert timed_out.reason == "field_heading_yaw_timeout"
