from __future__ import annotations

from execution.policy import ACTION_DISPATCH_POLICY


def test_atomic_actions_own_the_minimum_required_capabilities() -> None:
    expected = {
        "global_goto": {"goto_waypoint"},
        "flight_command": {"align_descend", "payload_release"},
        "body_velocity": {"align_descend"},
        "set_servo": {"payload_release"},
        "set_mode": {"takeoff"},
        "arm": {"takeoff"},
        "takeoff": {"takeoff"},
        "condition_yaw": {"takeoff"},
        "land": {"land"},
        "change_speed": {"change_speed"},
        "yolo_lock_target": {"target_lock", "gps_target_lock"},
        "clear_continuous_commands": {"align_descend"},
    }
    assert {name: rule.allowed_actions for name, rule in ACTION_DISPATCH_POLICY.items()} == expected


def test_every_vehicle_capability_keeps_required_safety_gates() -> None:
    for rule in ACTION_DISPATCH_POLICY.values():
        assert rule.requires_run_authorization is True
        assert rule.requires_send_commands is (False if rule is ACTION_DISPATCH_POLICY["yolo_lock_target"] else True)
