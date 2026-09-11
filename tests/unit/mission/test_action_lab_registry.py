from __future__ import annotations

import json
from pathlib import Path

from execution.policy import ACTION_DISPATCH_POLICY
from missions.common.actions.action_lab import (
    action_definitions,
    action_lab_specs,
    create_action_lab_registry,
)
from missions.common.actions.registry import default_registry

ROOT = Path(__file__).parents[3]


def test_action_definition_is_the_single_registry_and_web_catalog() -> None:
    definitions = action_definitions()
    registry = create_action_lab_registry()
    specs = action_lab_specs()
    names = [definition.name for definition in definitions]
    assert len(names) == len(set(names))
    assert registry.list() == sorted(names)
    assert [spec["name"] for spec in specs] == names
    json.dumps(specs)
    for definition, spec in zip(definitions, specs, strict=True):
        assert registry.create(definition.name).__class__ is definition.factory
        assert spec == definition.web_spec()
        assert spec["parameter_schema"]["type"] == "object"


def test_action_definition_defaults_match_full_v2_and_keep_send_boundaries() -> None:
    definitions = {definition.name: definition for definition in action_definitions()}
    full_v2 = json.loads(
        (ROOT / "config/action_missions/rescue_2026_full_auto.json").read_text()
    )
    first_step = {}
    for step in full_v2["steps"]:
        first_step.setdefault(step["name"], step["params"])

    for name in ("takeoff", "land", "change_speed"):
        assert definitions[name].default_params == first_step[name]

    align = dict(first_step["align_descend"])
    align["enabled"] = True
    assert definitions["align_descend"].default_params == align

    goto = definitions["goto_waypoint"].default_params
    expected_goto = dict(first_step["goto_waypoint"])
    expected_goto["field_x_m"] = expected_goto.pop("x")
    expected_goto["field_y_m"] = expected_goto.pop("y")
    assert goto == expected_goto

    capture = definitions["gps_capture_view"].default_params
    assert capture == first_step["gps_capture_view"]

    fuse = dict(first_step["gps_fuse_views"])
    fuse["views"] = []
    assert definitions["gps_fuse_views"].default_params == fuse

    select = dict(first_step["select_drop_targets"])
    select["objects"] = []
    assert definitions["select_drop_targets"].default_params == select

    payload = dict(first_step["payload_release"])
    payload["target_id"] = "target_debug"
    payload["servo_outputs"] = [
        {"channel": 8, "release_pwm": 1800, "hold_pwm": 1370},
    ]
    assert definitions["payload_release"].default_params == payload

    assert definitions["align_descend"].default_params["vx_sign"] == -1.0
    assert definitions["align_descend"].default_params["vy_sign"] == 1.0
    assert "manual_step" not in definitions
    assert not ({
        "select_recon_targets", "build_recon_report",
        "single_view_localize", "fixed_view_localize", "validate_target", "resolve_gps_targets",
    } & definitions.keys())
    assert "payload_release" in definitions
    assert "set_servo" in ACTION_DISPATCH_POLICY


def test_action_lab_does_not_mutate_default_registry() -> None:
    create_action_lab_registry()
    assert not (set(default_registry.list()) & {d.name for d in action_definitions()})


def test_goto_waypoint_global_dispatch_policy_enabled() -> None:
    assert "goto_waypoint" in ACTION_DISPATCH_POLICY["global_goto"].allowed_actions
