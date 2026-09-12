from __future__ import annotations

import json
from pathlib import Path

from missions.common.actions.action_lab import create_action_lab_registry
from missions.engine import MissionActionStep
from scripts.validate_action_missions import DEFAULT_TEMPLATE_PATHS, validate_templates

ROOT = Path(__file__).parents[3]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_only_three_formal_v2_templates_are_shipped() -> None:
    paths = sorted((ROOT / "config/action_missions").glob("*.json"))
    assert [path.name for path in paths] == [
        "drop_two_targets.json", "recon_gps.json", "rescue_2026_full_auto.json",
    ]
    assert all(_load(path)["version"] == 2 for path in paths)


def test_formal_templates_validate_and_actions_are_registered() -> None:
    assert len(validate_templates(DEFAULT_TEMPLATE_PATHS)) == 3
    registered = set(create_action_lab_registry().list())
    for path in DEFAULT_TEMPLATE_PATHS:
        for step in _load(path)["steps"]:
            assert step["name"] in registered
            assert step["on_failed"] == {"action": "continue"}
            MissionActionStep(step["name"], step["params"], save_as=step.get("save_as"),
                              label=step.get("label"), on_failed=step.get("on_failed"))


def test_every_template_goto_faces_the_fixed_field_positive_y_direction() -> None:
    for path in DEFAULT_TEMPLATE_PATHS:
        for step in _load(path)["steps"]:
            if step["name"] != "goto_waypoint":
                continue
            assert step["params"]["yaw_mode"] == "field_heading"
            assert step["params"]["field_yaw_deg"] == 0


def test_every_template_takeoff_uses_the_field_centerline_default() -> None:
    for path in DEFAULT_TEMPLATE_PATHS:
        takeoffs = [step for step in _load(path)["steps"] if step["name"] == "takeoff"]
        assert len(takeoffs) == 1
        assert takeoffs[0]["params"]["yaw_mode"] == "field_heading"
        assert takeoffs[0]["params"]["takeoff_yaw_deg"] is None


def test_drop_flow_is_explicit_and_preserves_payload_order_and_stop_boundary() -> None:
    steps = _load(ROOT / "config/action_missions/drop_two_targets.json")["steps"]
    names = [step["name"] for step in steps]
    assert "gps_multi_view_localize" not in names
    assert "gps_drop_sequence" not in names
    assert names.count("gps_capture_view") == 4
    assert names.count("gps_target_lock") == 0
    assert names.count("align_descend") == 2
    captures = [index for index, step in enumerate(steps) if step["name"] == "gps_capture_view"]
    for capture_index in captures:
        scan_goto = steps[capture_index - 1]
        assert scan_goto["name"] == "goto_waypoint"
        assert scan_goto["params"]["require_velocity_valid"] is True
        assert scan_goto["params"]["max_horizontal_speed_mps"] == 0.25
        assert scan_goto["params"]["max_vertical_speed_mps"] == 0.15
        assert scan_goto["params"]["min_hold_updates"] == 4
    releases = [step for step in steps if step["name"] == "payload_release"]
    assert [step["params"]["payload_id"] for step in releases] == ["payload_1", "payload_2"]
    assert [step["params"]["servo_outputs"] for step in releases] == [
        [{"channel": 8, "release_pwm": 1800, "hold_pwm": 1370}],
        [{"channel": 9, "release_pwm": 1745, "hold_pwm": 1325}],
    ]
    for release in releases:
        index = steps.index(release)
        assert steps[index - 1]["name"] == "align_descend"
        assert steps[index + 1]["name"] == "goto_waypoint"
    aligns = [step for step in steps if step["name"] == "align_descend"]
    assert all(
        "selection_mode" not in step["params"]
        and "target_valid" not in step["params"]
        and "track_id" not in step["params"]
        for step in aligns
    )


def test_recon_flow_contains_only_navigation_actions() -> None:
    steps = _load(ROOT / "config/action_missions/recon_gps.json")["steps"]
    names = [step["name"] for step in steps]
    assert "gps_recon_area_scan" not in names
    assert names == ["takeoff", "goto_waypoint", "goto_waypoint", "goto_waypoint", "goto_waypoint", "goto_waypoint", "goto_waypoint", "land"]


def test_full_flow_replaces_visual_land_composite_with_atomic_steps() -> None:
    steps = _load(ROOT / "config/action_missions/rescue_2026_full_auto.json")["steps"]
    names = [step["name"] for step in steps]
    assert "visual_land" not in names
    assert steps[-2]["label"] == "final_land_align"
    assert steps[-1]["name"] == "land"
    assert "final_land_lock_h" not in {step.get("label") for step in steps}
    assert "track_id" not in steps[-2]["params"]
    drop_aligns = [step for step in steps if step["name"] == "align_descend" and step["label"].startswith("drop_")]
    assert not [step for step in steps if step["name"] == "gps_target_lock"]
    assert all(
        "selection_mode" not in step["params"]
        and "target_valid" not in step["params"]
        and "track_id" not in step["params"]
        for step in drop_aligns
    )
    approaches = [step for step in steps if step.get("label") in {"drop_1_approach", "drop_2_approach"}]
    assert [step["params"]["altitude_m"] for step in approaches] == [2.5, 2.5]
    captures = [index for index, step in enumerate(steps) if step["name"] == "gps_capture_view"]
    for capture_index in captures:
        scan_goto = steps[capture_index - 1]
        assert scan_goto["name"] == "goto_waypoint"
        assert scan_goto["params"]["require_velocity_valid"] is True
        assert scan_goto["params"]["max_horizontal_speed_mps"] == 0.25
        assert scan_goto["params"]["max_vertical_speed_mps"] == 0.15
        assert scan_goto["params"]["min_hold_updates"] == 4


def test_full_flow_uses_the_fixed_down_sitl_camera_and_payload_contract() -> None:
    steps = _load(ROOT / "config/action_missions/rescue_2026_full_auto.json")["steps"]
    camera = {
        "fov_x_deg": 114.591559,
        "fov_y_deg": 98.864783,
        "image_x_sign": 1,
        "image_y_sign": -1,
    }

    for step in steps:
        if step["name"] in {"gps_capture_view", "gps_target_lock", "target_lock"}:
            assert step["params"]["camera"] == camera
        if step["name"] == "align_descend":
            assert "config" not in step["params"]

    releases = [step["params"] for step in steps if step["name"] == "payload_release"]
    assert releases[0]["servo_outputs"] == "$drop_targets.first_release_servo_outputs"
    assert releases[1]["servo_outputs"] == [
        {"channel": 9, "release_pwm": 1745, "hold_pwm": 1325},
    ]


def test_full_flow_plans_zero_one_or_two_target_release() -> None:
    steps = _load(ROOT / "config/action_missions/rescue_2026_full_auto.json")["steps"]
    by_label = {step["label"]: step for step in steps}
    selector = by_label["select_gps_drop_targets"]["params"]

    assert selector["fallback_target"] == {
        "valid": True,
        "id": "drop_zone_center",
        "target_id": "drop_zone_center",
        "x": 0,
        "y": 32.5,
        "status": "fallback_center",
    }
    assert selector["single_target_servo_outputs"] == [
        {"channel": 8, "release_pwm": 1800, "hold_pwm": 1370},
        {"channel": 9, "release_pwm": 1745, "hold_pwm": 1325},
    ]
    assert selector["multi_target_first_servo_outputs"] == [
        {"channel": 8, "release_pwm": 1800, "hold_pwm": 1370},
    ]

    assert by_label["drop_1_approach"]["params"]["target"] == "$drop_targets.target_slots.0"
    assert by_label["drop_2_approach"]["params"]["target"] == "$drop_targets.target_slots.1"
    assert by_label["drop_1_align"]["params"]["enabled"] == "$drop_targets.first_alignment_enabled"
    assert by_label["drop_2_align"]["params"]["enabled"] == "$drop_targets.second_alignment_enabled"
    assert by_label["drop_2_release"]["params"]["enabled"] == "$drop_targets.second_release_enabled"
    assert by_label["drop_1_align"]["params"]["complete_on_timeout"] is True
    assert by_label["drop_2_align"]["params"]["complete_on_timeout"] is True
    assert by_label["final_land_align"]["params"]["complete_on_timeout"] is False
