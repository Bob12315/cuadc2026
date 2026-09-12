"""The single Action definition catalog used by runtime, Web and validators."""
from __future__ import annotations

from contracts.core.action import ExitBarrier
from missions.definitions import ActionDefinition

from .align_descend import AlignDescendAction
from .change_speed import ChangeSpeedAction
from .goto_waypoint import GotoWaypointAction
from .gps_capture_view import GpsCaptureViewAction
from .gps_fuse_views import GpsFuseViewsAction
from .gps_target_lock import GpsTargetLockAction
from .land import LandAction
from .payload_release import PayloadReleaseAction
from .registry import ActionRegistry
from .select_drop_targets import SelectDropTargetsAction
from .takeoff import TakeoffAction
from .target_lock import TargetLockAction


def _definition(
    name: str,
    factory,
    description: str,
    defaults: dict | None = None,
    *,
    properties: dict | None = None,
    required_inputs: tuple[str, ...] = (),
    output_properties: dict | None = None,
    parameter_aliases: dict[str, tuple[str, ...]] | None = None,
    parameter_schema_extra: dict | None = None,
    effects: tuple[str, ...] = (),
    exit_barrier: ExitBarrier = ExitBarrier.NONE,
) -> ActionDefinition:
    return ActionDefinition(
        name=name,
        revision="v1",
        factory=factory,
        label=name.replace("_", " ").title(),
        description=description,
        default_params=dict(defaults or {}),
        parameter_schema={"type": "object", "properties": dict(properties or {}), "additionalProperties": False, **dict(parameter_schema_extra or {})},
        required_inputs=required_inputs,
        output_schema={"type": "object", "properties": dict(output_properties or {}), "additionalProperties": False},
        parameter_aliases=dict(parameter_aliases or {}),
        allowed_effect_types=effects,
        exit_barrier=exit_barrier,
    )


_NUMBER = {"type": "number"}
_NULLABLE_NUMBER = {"type": ["number", "null"]}
_INTEGER = {"type": "integer"}
_BOOLEAN = {"type": "boolean"}
_STRING = {"type": "string"}
_STRING_ARRAY = {"type": "array", "items": _STRING}
_ID = {"type": ["string", "integer"], "minLength": 1}
_CAMERA = {"type": "object", "properties": {
    "fov_x_deg": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 180},
    "fov_y_deg": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 180},
    "image_x_sign": {"type": "number", "enum": [-1, 1]},
    "image_y_sign": {"type": "number", "enum": [-1, 1]},
    "min_altitude_m": {"type": "number", "exclusiveMinimum": 0},
}, "additionalProperties": False}
_TARGET = {"type": ["object", "null"], "properties": {
    "id": _ID, "target_id": _ID, "valid": _BOOLEAN,
    "x": _NULLABLE_NUMBER, "y": _NULLABLE_NUMBER,
    "local_x": _NULLABLE_NUMBER, "local_y": _NULLABLE_NUMBER,
    "lat": {"type": ["number", "null"], "minimum": -90, "maximum": 90},
    "lon": {"type": ["number", "null"], "minimum": -180, "maximum": 180},
    "class_name": _STRING, "east_m": _NULLABLE_NUMBER, "north_m": _NULLABLE_NUMBER,
    "score": _NUMBER, "seen_count": _INTEGER, "count": _INTEGER,
    "raw_count": _INTEGER, "weight": _NUMBER, "track_ids": {"type": "array", "items": _INTEGER},
    "rank": _INTEGER, "status": _STRING,
}, "additionalProperties": False}
_LOCALIZED_OBJECT = {"type": "object", "properties": {
    "id": {"type": ["string", "integer"]}, "target_id": _ID, "valid": _BOOLEAN,
    "class_name": _STRING, "lat": _NUMBER, "lon": _NUMBER, "east_m": _NUMBER, "north_m": _NUMBER,
    "local_x": _NUMBER, "local_y": _NUMBER, "x": _NUMBER, "y": _NUMBER,
    "sample_count": _INTEGER, "seen_count": _INTEGER, "raw_count": _INTEGER, "count": _INTEGER,
    "confidence": _NUMBER, "weight": _NUMBER, "score": _NUMBER, "cluster_spread_m": _NUMBER,
    "source_waypoints": _STRING_ARRAY, "source_frames": {"type": "array", "items": _INTEGER},
    "track_ids": {"type": "array", "items": _INTEGER}, "rank": _INTEGER, "status": _STRING,
}, "additionalProperties": False}
_RAW_ESTIMATE = {"type": "object", "required": ["lat", "lon", "east_offset_m", "north_offset_m", "capture_drone_lat", "capture_drone_lon", "capture_yaw_rad", "capture_relative_altitude_m", "ex", "ey", "class_name"], "properties": {
    "lat": _NUMBER, "lon": _NUMBER, "east_offset_m": _NUMBER, "north_offset_m": _NUMBER,
    "capture_drone_lat": _NUMBER, "capture_drone_lon": _NUMBER, "capture_yaw_rad": _NUMBER,
    "capture_relative_altitude_m": _NUMBER, "ex": _NUMBER, "ey": _NUMBER, "class_name": _STRING,
    "confidence": {"type": ["number", "null"]}, "track_id": {"type": ["integer", "null"]},
    "frame_id": {"type": ["integer", "null"]}, "timestamp": {"type": ["number", "null"]},
    "source_waypoint": {"type": ["string", "null"]},
}, "additionalProperties": False}
_GPS_CAPTURE_OUTPUT = {"raw_estimates": {"type": "array", "items": _RAW_ESTIMATE}, "count": _INTEGER,
                       "source_waypoint": _STRING, "rejected_by_reason": {"type": "object", "patternProperties": {".*": {"type": "integer", "minimum": 0}}, "additionalProperties": False},
                       "coordinate_frame": {"type": "string", "const": "GLOBAL"}}
_SERVO_OUTPUT = {"type": "object", "required": ["channel", "release_pwm", "hold_pwm"], "properties": {
    "channel": {"type": "integer", "minimum": 1},
    "release_pwm": {"type": "integer", "minimum": 500, "maximum": 2500},
    "hold_pwm": {"type": "integer", "minimum": 500, "maximum": 2500},
}, "additionalProperties": False}
_DEFINITIONS = (
    _definition("takeoff", TakeoffAction, "Arm and take off through the execution safety gates.",
                {"mode": "GUIDED", "altitude_m": 4.5, "altitude_tolerance_m": 0.35, "require_armed": True, "max_updates": 150, "max_duration_s": 45, "priority": 2, "arm_priority": 1, "mode_priority": 2},
                properties={"mode": {"type": "string", "enum": ["GUIDED"]}, "altitude_m": {"type": "number", "exclusiveMinimum": 0}, "altitude_tolerance_m": {"type": "number", "exclusiveMinimum": 0}, "max_updates": {"type": "integer", "minimum": 1}, "max_duration_s": {"type": ["number", "null"], "exclusiveMinimum": 0}, "require_armed": _BOOLEAN, "priority": _INTEGER, "arm_priority": _INTEGER, "mode_priority": _INTEGER, "key": _STRING},
                required_inputs=("drone.mode", "drone.armed", "drone.relative_altitude"), effects=("set_mode", "arm", "takeoff")),
    _definition("land", LandAction, "Land through the execution safety gates.",
                {"max_updates": 300, "priority": 2},
                properties={"land_altitude_threshold_m": {"type": "number", "minimum": 0}, "max_updates": {"type": "integer", "minimum": 1}, "priority": _INTEGER, "key": _STRING}, required_inputs=("drone",), effects=("land",)),
    _definition("change_speed", ChangeSpeedAction, "Set the flight-controller speed target.", {"speed_mps": 1.0, "speed_type": "ground", "priority": 4, "key": "drop_speed_1mps"},
                properties={"speed_mps": {"type": "number", "exclusiveMinimum": 0}, "speed_type": {"type": "string", "enum": ["ground", "air", "climb", "descent"]}, "priority": _INTEGER, "key": _STRING}, effects=("change_speed",)),
    _definition("goto_waypoint", GotoWaypointAction, "Convert a FIELD target to GPS while holding yaw unless field-heading yaw is requested.",
                {"field_x_m": -3, "field_y_m": 31, "altitude_m": 4.5, "waypoint_mode": "field", "target_frame": "global", "yaw_mode": "hold", "tolerance_xy_m": 0.30, "tolerance_z_m": 0.30, "min_hold_updates": 4, "require_velocity_valid": True, "max_horizontal_speed_mps": 0.25, "max_vertical_speed_mps": 0.15, "position_hysteresis_xy_m": 0.20, "position_hysteresis_z_m": 0.10, "horizontal_speed_hysteresis_mps": 0.10, "vertical_speed_hysteresis_mps": 0.05, "reached_updates_decrement": 1, "control_reject_max_updates": 3, "control_reject_timeout_s": 1.0, "max_duration_s": 180.0, "priority": 5, "key": "drop_scan_goto_1"},
                properties={"field_x_m": _NUMBER, "field_y_m": _NUMBER, "x": _NUMBER, "y": _NUMBER, "lat": {"type": ["number", "null"], "minimum": -90, "maximum": 90}, "lon": {"type": ["number", "null"], "minimum": -180, "maximum": 180}, "altitude_m": {"type": "number", "exclusiveMinimum": 0}, "field_yaw_deg": _NUMBER, "yaw_deg": _NUMBER, "target": _TARGET, "target_valid": _BOOLEAN, "skip_if_invalid_target": _BOOLEAN, "tolerance_xy_m": {"type": "number", "exclusiveMinimum": 0}, "tolerance_z_m": {"type": "number", "exclusiveMinimum": 0}, "min_hold_updates": {"type": "integer", "minimum": 1}, "require_velocity_valid": _BOOLEAN, "max_horizontal_speed_mps": {"type": "number", "minimum": 0}, "max_vertical_speed_mps": {"type": "number", "minimum": 0}, "position_hysteresis_xy_m": {"type": "number", "minimum": 0}, "position_hysteresis_z_m": {"type": "number", "minimum": 0}, "horizontal_speed_hysteresis_mps": {"type": "number", "minimum": 0}, "vertical_speed_hysteresis_mps": {"type": "number", "minimum": 0}, "reached_updates_decrement": {"type": "integer", "minimum": 0}, "control_reject_max_updates": {"type": "integer", "minimum": 1}, "control_reject_timeout_s": {"type": "number", "exclusiveMinimum": 0}, "max_duration_s": {"type": "number", "exclusiveMinimum": 0}, "priority": _INTEGER, "key": _STRING, "waypoint_mode": {"type": "string", "enum": ["field", "absolute"]}, "target_frame": {"type": "string", "enum": ["global"]}, "yaw_mode": {"type": "string", "enum": ["field_heading", "hold"]}},
                parameter_aliases={"field_x_m": ("x",), "field_y_m": ("y",), "field_yaw_deg": ("yaw_deg",)},
                parameter_schema_extra={"required": ["altitude_m"], "anyOf": [{"required": ["lat", "lon"]}, {"required": ["field_x_m", "field_y_m"]}, {"required": ["target"]}]},
                required_inputs=("field_reference", "drone.global_position"), effects=("global_goto",)),
    _definition("target_lock", TargetLockAction, "Select and lock a visual target.",
                {"acquire_mode": "known_target", "max_match_distance_m": 1.0, "min_confidence": 0.0, "detection_source": "scene", "max_updates": 30, "max_target_age_s": 0.5, "require_unique_track": True, "priority": 5, "lock_once": True},
                properties={"target": _TARGET, "skip_if_invalid_target": _BOOLEAN, "acquire_mode": {"type": "string", "enum": ["known_target", "class_single"]}, "max_match_distance_m": {"type": "number", "exclusiveMinimum": 0}, "min_confidence": {"type": "number", "minimum": 0}, "detection_source": {"type": "string", "enum": ["scene", "perception"]}, "max_updates": {"type": "integer", "minimum": 1}, "max_target_age_s": {"type": "number", "exclusiveMinimum": 0}, "require_unique_track": _BOOLEAN, "class_names": _STRING_ARRAY, "camera": _CAMERA, "priority": _INTEGER, "key": _STRING, "lock_once": _BOOLEAN}, output_properties={"locked_track_id": _INTEGER}, required_inputs=("scene", "drone"), effects=("yolo_lock_target",)),
    _definition(
        "align_descend",
        AlignDescendAction,
        "Continuously align to the scene target nearest the image centre and descend.",
        {"enabled": True, "complete_on_timeout": True, "target_altitude_m": 1.2, "descend_speed_mps": 0.3, "release_deadband_ex": 0.02, "release_deadband_ey": 0.02, "kp_forward": 0.3, "kp_right": 0.3, "max_vx_mps": 0.25, "max_vy_mps": 0.25, "vx_sign": -1.0, "vy_sign": 1.0, "field_yaw_deg": 0.0, "priority": 5, "key": "drop_1_align"},
        properties={"enabled": _BOOLEAN, "complete_on_timeout": _BOOLEAN, "target_altitude_m": {"type": "number", "exclusiveMinimum": 0}, "descend_speed_mps": {"type": "number", "minimum": 0}, "release_deadband_ex": {"type": "number", "exclusiveMinimum": 0}, "release_deadband_ey": {"type": "number", "exclusiveMinimum": 0}, "priority": _INTEGER, "key": _STRING, "max_vx_mps": {"type": "number", "exclusiveMinimum": 0}, "max_vy_mps": {"type": "number", "exclusiveMinimum": 0}, "kp_forward": {"type": "number", "minimum": 0}, "kp_right": {"type": "number", "minimum": 0}, "vx_sign": {"type": "number", "enum": [-1, 1]}, "vy_sign": {"type": "number", "enum": [-1, 1]}, "field_yaw_deg": _NUMBER, "desired_yaw_deg": {"type": ["number", "null"]}},
        required_inputs=("scene", "drone.relative_altitude", "field_heading_yaw_rad"), effects=("flight_command",), exit_barrier=ExitBarrier.MOTION_STOPPED),
    _definition("payload_release", PayloadReleaseAction, "Release a whitelisted payload servo through execution.", {"enabled": True, "payload_id": "payload_1", "target_id": "target_debug", "servo_outputs": [{"channel": 8, "release_pwm": 1800, "hold_pwm": 1370}], "priority": 3, "release_wait_s": 1, "release_wait_updates": 5, "key": "drop_1_release"}, properties={"enabled": _BOOLEAN, "payload_id": _ID, "target_id": _ID, "servo_outputs": {"type": "array", "minItems": 1, "items": _SERVO_OUTPUT}, "servo_channels": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1}}, "channels": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1}}, "channel": {"type": "integer", "minimum": 1}, "release_pwm": {"type": "integer", "minimum": 500, "maximum": 2500}, "hold_pwm": {"type": "integer", "minimum": 500, "maximum": 2500}, "release_wait_updates": {"type": "integer", "minimum": 1}, "release_wait_s": {"type": ["number", "null"], "exclusiveMinimum": 0}, "priority": _INTEGER, "key": _STRING, "release_time": {"type": ["number", "string", "null"]}}, parameter_schema_extra={"required": ["payload_id", "target_id"]}, effects=("set_servo",)),
    _definition("select_drop_targets", SelectDropTargetsAction, "Select ranked drop targets.", {"objects": [], "coordinate_mode": "gps_enu", "target_count": 2, "allow_fewer": True, "fallback_target": {"valid": True, "id": "drop_zone_center", "target_id": "drop_zone_center", "x": 0.0, "y": 32.5, "status": "fallback_center"}, "single_target_servo_outputs": [{"channel": 8, "release_pwm": 1800, "hold_pwm": 1370}, {"channel": 9, "release_pwm": 1745, "hold_pwm": 1325}], "multi_target_first_servo_outputs": [{"channel": 8, "release_pwm": 1800, "hold_pwm": 1370}], "score_table": {"bucket_1": 500, "bucket_2": 300, "bucket_3": 100, "bucket": 50}, "min_seen_count": 3, "min_raw_count": 3, "min_weight": 0.0, "deduplicate_radius_m": 0.8, "prefer_class_order": ["bucket_1", "bucket_2", "bucket_3", "bucket"], "key": "select_gps_drop_targets"}, properties={"objects": {"type": "array", "items": _LOCALIZED_OBJECT}, "input_key": _STRING, "target_count": {"type": "integer", "minimum": 1}, "min_seen_count": {"type": "integer", "minimum": 0}, "min_raw_count": {"type": "integer", "minimum": 0}, "min_weight": {"type": "number", "minimum": 0}, "deduplicate_radius_m": {"type": "number", "minimum": 0}, "score_table": {"type": "object", "patternProperties": {".*": {"type": "number"}}, "additionalProperties": False}, "prefer_class_order": _STRING_ARRAY, "allow_fewer": _BOOLEAN, "require_local_xy": _BOOLEAN, "fallback_target": _TARGET, "single_target_servo_outputs": {"type": ["array", "null"], "items": _SERVO_OUTPUT}, "multi_target_first_servo_outputs": {"type": ["array", "null"], "items": _SERVO_OUTPUT}, "key": _STRING, "zone_center": {"type": ["array", "null"], "minItems": 2, "maxItems": 2, "items": _NUMBER}, "zone_center_mode": {"type": "string", "enum": ["local", "field"]}, "coordinate_mode": {"type": "string", "enum": ["local", "gps_enu"]}}, output_properties={"selected_targets": {"type": "array", "items": _TARGET}, "target_slots": {"type": "array", "items": _TARGET}, "selected_count": _INTEGER, "candidate_count": _INTEGER, "first_release_servo_outputs": {"type": "array", "items": _SERVO_OUTPUT}, "first_alignment_enabled": _BOOLEAN, "second_alignment_enabled": _BOOLEAN, "second_release_enabled": _BOOLEAN}, required_inputs=("field_reference",)),
    _definition("gps_capture_view", GpsCaptureViewAction, "Project current-view detections to GPS without moving.", {"source_waypoint": "DROP_SCAN_1", "class_names": ["bucket_1", "bucket_2", "bucket_3", "bucket"], "min_confidence": 0.3, "camera": {"fov_x_deg": 114.591559, "fov_y_deg": 98.864783, "image_x_sign": 1, "image_y_sign": -1}}, properties={"class_names": _STRING_ARRAY, "min_confidence": {"type": "number", "minimum": 0}, "source_waypoint": _STRING, "camera": _CAMERA}, output_properties=_GPS_CAPTURE_OUTPUT, required_inputs=("scene", "drone")),
    _definition("gps_fuse_views", GpsFuseViewsAction, "Fuse Mission-captured GPS views.", {"views": [], "class_names": ["bucket_1", "bucket_2", "bucket_3", "bucket"], "fusion": {"cluster_radius_m": 0.65, "outlier_radius_m": 0.65, "min_cluster_size": 3, "min_source_waypoints": 3, "center_weight_power": 1, "max_abs_ex": 0.8, "max_abs_ey": 0.65}}, properties={"views": {"type": "array", "items": {"type": "object", "properties": _GPS_CAPTURE_OUTPUT, "additionalProperties": False}}, "fusion": {"type": "object", "properties": {"cluster_radius_m": {"type": "number", "minimum": 0}, "outlier_radius_m": {"type": "number", "minimum": 0}, "min_cluster_size": {"type": "integer", "minimum": 1}, "min_source_waypoints": {"type": "integer", "minimum": 1}, "center_weight_power": {"type": "number", "minimum": 0}, "min_confidence": {"type": "number", "minimum": 0}, "max_abs_ex": {"type": ["number", "null"]}, "max_abs_ey": {"type": ["number", "null"]}}, "additionalProperties": False}, "class_names": _STRING_ARRAY}, output_properties={"localized_objects": {"type": "array", "items": _LOCALIZED_OBJECT}, "objects": {"type": "array", "items": _LOCALIZED_OBJECT}, "raw_estimates_count": _INTEGER, "count": _INTEGER, "coordinate_frame": {"type": "string", "const": "GLOBAL"}}, required_inputs=("field_reference",)),
    _definition("gps_target_lock", GpsTargetLockAction, "Lock and confirm a visual track matching a captured GPS target.", {"max_match_distance_m": 1.2, "selection_mode": "nearest_gps", "min_match_margin_m": 0.0, "max_updates": 40, "min_confidence": 0.35, "class_names": [], "camera": {"fov_x_deg": 51.3, "fov_y_deg": 39.6}, "detection_source": "scene", "require_track_id": True, "require_class_match": True, "require_lock_confirmation": True, "max_target_age_s": 0.5}, properties={"target": _TARGET, "max_match_distance_m": {"type": "number", "exclusiveMinimum": 0}, "selection_mode": {"type": "string", "enum": ["nearest_gps", "nearest_image_center"]}, "min_match_margin_m": {"type": "number", "minimum": 0}, "max_updates": {"type": "integer", "minimum": 1}, "min_confidence": {"type": "number", "minimum": 0}, "class_names": _STRING_ARRAY, "camera": _CAMERA, "detection_source": {"type": "string", "enum": ["scene", "perception"]}, "require_track_id": _BOOLEAN, "require_class_match": _BOOLEAN, "require_lock_confirmation": _BOOLEAN, "max_target_age_s": {"type": "number", "exclusiveMinimum": 0}}, parameter_schema_extra={"required": ["target"]}, output_properties={"locked_track_id": _INTEGER}, required_inputs=("scene", "drone", "perception"), effects=("yolo_lock_target",)),
)


def action_definitions() -> tuple[ActionDefinition, ...]:
    return _DEFINITIONS


def action_definition(name: str) -> ActionDefinition:
    for definition in _DEFINITIONS:
        if definition.name == name:
            return definition
    raise KeyError(f"unknown action definition: {name}")


def create_action_lab_registry() -> ActionRegistry:
    registry = ActionRegistry()
    for definition in _DEFINITIONS:
        registry.register(definition.name, definition.factory)
    return registry


def action_lab_specs() -> list[dict]:
    return [definition.web_spec() for definition in _DEFINITIONS]
