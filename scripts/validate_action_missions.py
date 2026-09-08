from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from missions.common.actions.action_lab import (
    action_definition,
    create_action_lab_registry,
)
from missions.engine import MissionBlackboard

DEFAULT_TEMPLATE_PATHS = [
    ROOT / "config/action_missions/drop_two_targets.json",
    ROOT / "config/action_missions/recon_gps.json",
    ROOT / "config/action_missions/rescue_2026_full_auto.json",
]
ALLOWED_FAILURE_ACTIONS = {"fail", "retry_current", "retry_current_then_jump_to", "jump_to", "continue"}


def validate_templates(paths: list[Path]) -> list[str]:
    registered_actions = set(create_action_lab_registry().list())
    messages: list[str] = []
    for path in paths:
        data = _load_template(path)
        steps = _validate_shape(path, data)
        labels = _validate_steps(path, steps, registered_actions)
        _validate_blackboard_refs(path, steps)
        display_path = _display_path(path)
        label_text = ",".join(sorted(labels)) if labels else "-"
        messages.append(f"OK {display_path} steps={len(steps)} labels={label_text}")
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Action Mission JSON templates offline.")
    parser.add_argument("paths", nargs="*", type=Path, help="Template path(s) to validate.")
    args = parser.parse_args(argv)

    paths = args.paths or DEFAULT_TEMPLATE_PATHS
    try:
        messages = validate_templates(paths)
    except ValueError as exc:
        print(str(exc))
        return 1

    for message in messages:
        print(message)
    print("All action mission templates validated.")
    return 0


def _load_template(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"ERROR {_display_path(path)}: cannot read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"ERROR {_display_path(path)}: top-level JSON must be an object")
    return data


def _validate_shape(path: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError(f"ERROR {_display_path(path)}: top-level name must be a non-empty string")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"ERROR {_display_path(path)}: steps must be a non-empty list")
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"ERROR {_display_path(path)}: step {index} must be an object")
    return steps


def _validate_steps(
    path: Path,
    steps: list[dict[str, Any]],
    registered_actions: set[str],
) -> set[str]:
    labels: dict[str, int] = {}
    for index, step in enumerate(steps):
        prefix = f"ERROR {_display_path(path)}: step {index}"
        name = step.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{prefix} name must be a non-empty string")
        if name.strip() not in registered_actions:
            raise ValueError(f"{prefix} unknown action: {name}")

        params = step.get("params")
        if not isinstance(params, dict):
            raise ValueError(f"{prefix} params must be an object")

        save_as = step.get("save_as")
        if save_as is not None and (not isinstance(save_as, str) or not save_as.strip()):
            raise ValueError(f"{prefix} save_as must be a non-empty string")

        label = step.get("label")
        if label is not None:
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"{prefix} label must be a non-empty string")
            normalized = label.strip()
            if normalized in labels:
                raise ValueError(f"{prefix} duplicate label: {normalized}")
            labels[normalized] = index

        _validate_on_failed(path, index, step.get("on_failed"))

    label_set = set(labels)
    for index, step in enumerate(steps):
        policy = step.get("on_failed")
        if not isinstance(policy, dict) or policy.get("action") not in {"jump_to", "retry_current_then_jump_to"}:
            continue
        target = policy.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"ERROR {_display_path(path)}: step {index} {policy.get('action')} target must be a non-empty string")
        if target.strip() not in label_set:
            raise ValueError(f"ERROR {_display_path(path)}: step {index} {policy.get('action')} target not found: {target}")
    return label_set


def _validate_on_failed(path: Path, index: int, policy: Any) -> None:
    if policy is None:
        return
    prefix = f"ERROR {_display_path(path)}: step {index}"
    if not isinstance(policy, dict):
        raise ValueError(f"{prefix} on_failed must be an object")
    action = policy.get("action", "fail")
    if not isinstance(action, str) or action not in ALLOWED_FAILURE_ACTIONS:
        raise ValueError(f"{prefix} invalid on_failed action: {action}")
    if action in {"retry_current", "retry_current_then_jump_to", "jump_to"}:
        attempts = policy.get("max_attempts", 1)
        if not isinstance(attempts, int) or attempts < 1:
            raise ValueError(f"{prefix} {action}.max_attempts must be >= 1")


def _validate_blackboard_refs(path: Path, steps: list[dict[str, Any]]) -> None:
    blackboard = _smoke_blackboard()
    for index, step in enumerate(steps):
        params = step["params"]
        for ref in _blackboard_refs(params):
            if ref == "$":
                raise ValueError(f"ERROR {_display_path(path)}: step {index} blackboard path must be non-empty")
        try:
            resolved_params = blackboard.resolve(params)
        except Exception as exc:
            raise ValueError(
                f"ERROR {_display_path(path)}: step {index} blackboard resolve failed: {exc}"
            ) from exc
        try:
            action_definition(str(step["name"])).merge_and_validate_params(resolved_params)
        except Exception as exc:
            label = step.get("label") or "-"
            raise ValueError(
                f"ERROR {_display_path(path)}: step {index} label={label} action={step['name']} parameter validation failed: {exc}"
            ) from exc
        save_as = step.get("save_as")
        if save_as:
            blackboard.set(save_as, _example_output(str(step["name"])))


def _blackboard_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith("$") else []
    if isinstance(value, dict):
        refs: list[str] = []
        for item in value.values():
            refs.extend(_blackboard_refs(item))
        return refs
    if isinstance(value, list):
        refs = []
        for item in value:
            refs.extend(_blackboard_refs(item))
        return refs
    return []


def _smoke_blackboard() -> MissionBlackboard:
    return MissionBlackboard()


def _example_output(action_name: str) -> dict[str, Any]:
    """Minimal valid business output for sequential Blackboard path checks.

    These are validator fixtures, not a second Action contract: each value is
    constrained by and shaped after the Action's output schema.
    """
    raw_estimate = {"lat": 34.0, "lon": 108.0, "east_offset_m": 1.0, "north_offset_m": 1.0,
                    "capture_drone_lat": 34.0, "capture_drone_lon": 108.0, "capture_yaw_rad": 0.0,
                    "capture_relative_altitude_m": 4.0, "ex": 0.0, "ey": 0.0, "class_name": "bucket_1",
                    "confidence": 0.9, "track_id": 1, "frame_id": 1, "timestamp": 0.0, "source_waypoint": "view"}
    if action_name == "gps_capture_view":
        return {"raw_estimates": [raw_estimate], "count": 1, "source_waypoint": "view",
                "rejected_by_reason": {}, "coordinate_frame": "GLOBAL"}
    if action_name == "gps_fuse_views":
        localized = {"id": 1, "lat": 34.0, "lon": 108.0, "east_m": 1.0, "north_m": 1.0,
                     "sample_count": 2, "raw_count": 2, "class_name": "bucket_1", "confidence": 0.9,
                     "cluster_spread_m": 0.1, "source_waypoints": ["view"], "source_frames": [1]}
        return {"localized_objects": [localized], "objects": [localized], "raw_estimates_count": 1,
                "count": 1, "coordinate_frame": "GLOBAL"}
    if action_name == "select_drop_targets":
        slot = {"valid": True, "id": "bucket_1", "target_id": "bucket_1", "class_name": "bucket_1",
                "local_x": 1.0, "local_y": 1.0, "x": 1.0, "y": 1.0, "east_m": 1.0, "north_m": 1.0,
                "lat": 34.0, "lon": 108.0, "score": 500.0, "seen_count": 2, "count": 2,
                "raw_count": 2, "weight": 1.0, "track_ids": [1], "rank": 1}
        servo = {"channel": 9, "release_pwm": 1800, "hold_pwm": 1600}
        return {
            "selected_targets": [slot],
            "target_slots": [slot, dict(slot, rank=2)],
            "selected_count": 2,
            "candidate_count": 2,
            "first_release_servo_outputs": [servo],
            "first_alignment_enabled": True,
            "second_alignment_enabled": True,
            "second_release_enabled": True,
        }
    if action_name in {"target_lock", "gps_target_lock"}:
        return {"locked_track_id": 1}
    return {}


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
