from __future__ import annotations

import json
from pathlib import Path

from missions.common.actions.action_lab import action_definitions, action_lab_specs

ROOT = Path(__file__).parents[3]


def _steps(name: str) -> list[dict]:
    return json.loads((ROOT / f"config/action_missions/{name}").read_text())["steps"]


def _align(name: str) -> list[dict]:
    return [step["params"] for step in _steps(name) if step["name"] == "align_descend" and step["label"].startswith("drop_")]


def test_atomic_drop_alignment_tuning_is_minimal() -> None:
    generic = _align("drop_two_targets.json")
    rescue = _align("rescue_2026_full_auto.json")
    assert len(generic) == len(rescue) == 2
    allowed = {
        "target_altitude_m", "descend_speed_mps", "release_deadband_ex",
        "release_deadband_ey", "kp_forward", "kp_right", "max_vx_mps",
        "max_vy_mps", "vx_sign", "vy_sign", "field_yaw_deg", "priority", "key",
    }
    assert all(set(item) == allowed for item in generic)
    assert all(set(item) == allowed | {"enabled", "complete_on_timeout"} for item in rescue)
    for item in generic:
        assert item["target_altitude_m"] == 1.2
        assert item["descend_speed_mps"] == 0.24
        assert (item["release_deadband_ex"], item["release_deadband_ey"]) == (0.02, 0.02)
        assert (item["kp_forward"], item["kp_right"]) == (0.275, 0.275)
        assert (item["max_vx_mps"], item["max_vy_mps"]) == (0.2, 0.2)
    for item in rescue:
        assert item["complete_on_timeout"] is True
        assert item["target_altitude_m"] == 1.2
        assert item["descend_speed_mps"] == 0.30
        assert (item["release_deadband_ex"], item["release_deadband_ey"]) == (0.02, 0.02)
        assert (item["kp_forward"], item["kp_right"]) == (0.3, 0.3)
        assert (item["max_vx_mps"], item["max_vy_mps"]) == (0.25, 0.25)


def test_capture_camera_and_recon_routes_are_preserved() -> None:
    drop = _steps("drop_two_targets.json")
    cameras = [step["params"]["camera"] for step in drop if step["name"] == "gps_capture_view"]
    assert len(cameras) == 4
    assert all((camera["fov_x_deg"], camera["fov_y_deg"]) == (114.591559, 98.864783) for camera in cameras)
    scan_holds = [step["params"]["min_hold_updates"] for step in drop if step.get("label", "").startswith("drop_scan_goto_")]
    assert scan_holds == [4, 4, 4, 4]
    recon = _steps("recon_gps.json")
    points = [(step["params"]["x"], step["params"]["y"], step["params"]["altitude_m"])
              for step in recon if step.get("label", "").startswith("recon_scan_goto_")]
    assert points == [(-3, 56, 3), (3, 56, 3), (3, 58, 3), (-3, 58, 3)]


def test_profile_contains_no_mission_template_copies() -> None:
    profile_dir = ROOT / "config/profiles/rk3588-sitl/action_missions"
    assert not profile_dir.exists() or list(profile_dir.glob("*.json")) == []


def test_action_lab_public_defaults_are_owned_by_definitions() -> None:
    assert action_lab_specs() == [definition.web_spec() for definition in action_definitions()]
