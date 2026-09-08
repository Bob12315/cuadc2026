from __future__ import annotations

import math

from missions.common.actions.goto_waypoint import GotoWaypointAction


def _context() -> dict:
    return {
        "field_heading_confirmed": True,
        "field_origin_gps_confirmed": True,
        "field_heading_yaw_rad": math.pi / 4,
        "field_origin_lat": 34.0,
        "field_origin_lon": 108.0,
        "field_reference": {
            "is_confirmed": True,
            "synced_to_runtime": True,
            "is_frozen": True,
            "is_ready_for_field_to_gps": True,
        },
        "drone": {"global_position_valid": False, "attitude_valid": True, "yaw": -math.pi / 3},
    }


def _active_action(params: dict) -> dict:
    action = GotoWaypointAction()
    action.start({"field_x_m": 0.0, "field_y_m": 1.0, "altitude_m": 3.0} | params)
    result = action.update(_context())
    assert result.reason == "waiting_for_global_position"
    assert len(result.actions) == 1
    return result.actions[0]


def test_hold_yaw_mode_snapshots_current_yaw_in_global_goto() -> None:
    request = _active_action({"yaw_mode": "hold"})

    assert request["action_type"] == "global_goto"
    assert math.isclose(request["params"]["yaw"], -math.pi / 3)


def test_field_heading_yaw_mode_sends_explicit_yaw() -> None:
    request = _active_action({"yaw_mode": "field_heading", "field_yaw_deg": 90.0})

    assert math.isclose(request["params"]["yaw"], 3 * math.pi / 4)


def test_target_object_can_supply_field_coordinates() -> None:
    action = GotoWaypointAction()
    action.start({
        "target": {"valid": True, "x": 0.0, "y": 32.5},
        "altitude_m": 2.5,
        "yaw_mode": "hold",
    })

    result = action.update(_context())

    assert result.reason == "waiting_for_global_position"
    assert result.detail["input_kind"] == "field"
    assert result.detail["field_x_m"] == 0.0
    assert result.detail["field_y_m"] == 32.5
    assert result.detail["global_target"]["alt"] == 2.5


def test_invalid_target_object_skips_before_coordinate_parsing() -> None:
    action = GotoWaypointAction()
    action.start({
        "target": {"valid": False, "lat": None, "lon": None},
        "altitude_m": 2.5,
        "skip_if_invalid_target": True,
    })

    result = action.update({})

    assert result.done and not result.failed
    assert result.reason == "skipped_missing_target"
    assert result.actions == []
