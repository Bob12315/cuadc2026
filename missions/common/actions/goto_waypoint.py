from __future__ import annotations

import math
import time
from typing import Any

from contracts.frames import GLOBAL_RELATIVE_ALT_INT
from field.coordinates import field_to_gps
from field.models import FieldReference, FieldReferenceError

from .base import ActionModule
from .result import ActionResult


class GotoWaypointAction(ActionModule):
    """Convert a FIELD target to GPS without changing yaw by default.

    This Action emits only ``global_goto``.  ``yaw_mode=hold`` snapshots the
    first valid vehicle yaw and includes it in every MAVLink target, preventing
    GUIDED mode from automatically turning the vehicle toward the waypoint.
    ``yaw_mode=field_heading`` uses ``field_yaw_deg`` clockwise from FIELD +Y
    and converts it to the north-referenced yaw used by MAVLink.
    ``lat``/``lon`` are accepted only as a migration input for a target already
    resolved by FIELD, and still use the frozen Field heading for yaw.
    """

    def __init__(self) -> None:
        self.reset()

    def start(self, params: dict[str, Any] | None = None) -> None:
        data = params or {}
        self.skip_if_invalid_target = bool(data.get("skip_if_invalid_target", False))
        self.altitude_m = self._required_float(data, "altitude_m")
        if self.altitude_m <= 0.0:
            raise ValueError("altitude_m must be positive")
        target = data.get("target")
        self._skipped = self.skip_if_invalid_target and (
            (isinstance(target, dict) and target.get("valid") is False)
            or data.get("target_valid") is False
        )
        coordinate_source = target if isinstance(target, dict) else data
        if self._skipped:
            self.input_kind = "skipped"
            self.field_x_m = self.field_y_m = self.lat = self.lon = None
        elif coordinate_source.get("lat") is not None and coordinate_source.get("lon") is not None:
            self.input_kind = "resolved_gps"
            self.lat = self._required_float(coordinate_source, "lat")
            self.lon = self._required_float(coordinate_source, "lon")
            if not -90.0 <= self.lat <= 90.0 or not -180.0 <= self.lon <= 180.0:
                raise ValueError("GPS target is out of WGS84 range")
            self.field_x_m = self.field_y_m = None
        else:
            self.input_kind = "field"
            # x/y are temporary spelling compatibility; they mean FIELD, never LOCAL_NED.
            self.field_x_m = self._required_float(coordinate_source, "field_x_m", "x")
            self.field_y_m = self._required_float(coordinate_source, "field_y_m", "y")
            self.lat = self.lon = None

        yaw_mode = str(data.get("yaw_mode", "")).strip().lower()
        if not yaw_mode:
            yaw_mode = "field_heading" if any(
                name in data for name in ("field_yaw_deg", "yaw_deg")
            ) else "hold"
        if yaw_mode not in {"hold", "field_heading"}:
            raise ValueError("yaw_mode must be 'hold' or 'field_heading'")
        self.yaw_mode = yaw_mode
        self.field_yaw_deg = self._finite_float(
            data.get("field_yaw_deg", data.get("yaw_deg", 0.0)), "field_yaw_deg"
        )
        self.tolerance_xy_m = self._positive_float(data.get("tolerance_xy_m", 0.30), "tolerance_xy_m")
        self.tolerance_z_m = self._positive_float(data.get("tolerance_z_m", 0.30), "tolerance_z_m")
        self.min_hold_updates = max(1, int(data.get("min_hold_updates", 4)))
        self.require_velocity_valid = bool(data.get("require_velocity_valid", False))
        self.max_horizontal_speed_mps = self._non_negative_float(data.get("max_horizontal_speed_mps", 0.25), "max_horizontal_speed_mps")
        self.max_vertical_speed_mps = self._non_negative_float(data.get("max_vertical_speed_mps", 0.15), "max_vertical_speed_mps")
        # Arrival telemetry is noisy on real aircraft.  The normal gate below
        # is deliberately strict; these margins describe the nearby region in
        # which one noisy sample merely decays progress instead of discarding
        # the whole arrival observation window.
        self.position_hysteresis_xy_m = self._non_negative_float(
            data.get("position_hysteresis_xy_m", 0.20), "position_hysteresis_xy_m"
        )
        self.position_hysteresis_z_m = self._non_negative_float(
            data.get("position_hysteresis_z_m", 0.10), "position_hysteresis_z_m"
        )
        self.horizontal_speed_hysteresis_mps = self._non_negative_float(
            data.get("horizontal_speed_hysteresis_mps", 0.10),
            "horizontal_speed_hysteresis_mps",
        )
        self.vertical_speed_hysteresis_mps = self._non_negative_float(
            data.get("vertical_speed_hysteresis_mps", 0.05),
            "vertical_speed_hysteresis_mps",
        )
        self.reached_updates_decrement = max(
            0, int(data.get("reached_updates_decrement", 1))
        )
        self.control_reject_max_updates = max(
            1, int(data.get("control_reject_max_updates", 3))
        )
        self.control_reject_timeout_s = self._positive_float(
            data.get("control_reject_timeout_s", 1.0),
            "control_reject_timeout_s",
        )
        self.max_duration_s = self._positive_float(
            data.get("max_duration_s", 180.0), "max_duration_s"
        )
        self.priority = int(data.get("priority", 4))
        self.key = str(data.get("key") or "goto_field_gps").strip() or "goto_field_gps"
        self.started, self.stopped, self.reached_updates = True, False, 0
        self.reached_update_action = "reset"
        self.control_reject_count = 0
        self.control_reject_started_monotonic: float | None = None
        self.control_reject_reason: str | None = None
        self.control_reject_elapsed_s = 0.0
        self.started_monotonic = None
        self.last_now_monotonic = None

    def update(self, context: dict[str, Any] | None = None) -> ActionResult:
        if not self.started:
            return ActionResult(failed=True, reason="action_not_started")
        if self.stopped:
            return ActionResult(done=True, reason="stopped")
        if self._skipped:
            return ActionResult(done=True, reason="skipped_missing_target")
        context_data = context or {}
        reference = self._field_reference(context_data)
        if reference is None:
            return ActionResult(failed=True, reason="field_reference_not_ready")
        try:
            target = self._global_target(reference)
        except FieldReferenceError as exc:
            return ActionResult(failed=True, reason="field_to_gps_failed", detail={"error": str(exc)})
        now_monotonic = self._context_monotonic(context_data)
        self.last_now_monotonic = now_monotonic
        if self.started_monotonic is None:
            self.started_monotonic = now_monotonic
        elapsed_s = max(0.0, now_monotonic - self.started_monotonic)
        if elapsed_s >= self.max_duration_s:
            return ActionResult(
                failed=True,
                reason="goto_timeout",
                detail=self._detail(target, None, None, None, None),
            )
        if self.yaw_mode == "field_heading":
            yaw_rad = self._normalize_yaw(
                float(reference.field_heading_yaw_rad) + math.radians(self.field_yaw_deg)
            )
        else:
            if self.hold_yaw_rad is None:
                current_yaw = self._current_yaw(context_data)
                if current_yaw is None:
                    return ActionResult(
                        reason="waiting_for_attitude",
                        detail=self._detail(target, None, None, None, None),
                    )
                self.hold_yaw_rad = self._normalize_yaw(current_yaw)
            yaw_rad = self.hold_yaw_rad
        control_rejection = self._control_rejection(context_data)
        if control_rejection is not None:
            control_rejected = self._record_control_rejection(
                control_rejection, now_monotonic
            )
            detail = self._detail(target, yaw_rad, None, None, None)
            if control_rejected:
                return ActionResult(
                    failed=True,
                    reason=control_rejection,
                    detail=detail,
                )
            return ActionResult(
                reason="control_not_allowed_pending",
                detail=detail,
            )
        self._clear_control_rejection_if_explicitly_allowed(context_data)
        if self._control_rejection_exceeded(now_monotonic):
            detail = self._detail(target, yaw_rad, None, None, None)
            return ActionResult(
                failed=True,
                reason=self.control_reject_reason or "control_not_allowed",
                detail=detail,
            )
        current = self._current_global_position(context_data)
        if current is None:
            return ActionResult(
                effects=ActionResult.typed([self._effect(target, yaw_rad, context_data)]),
                reason="waiting_for_global_position",
                detail=self._detail(target, yaw_rad, None, None, None),
            )
        distance = self._gps_distance_m(current["lat"], current["lon"], target["lat"], target["lon"])
        z_error = abs(current["alt"] - target["alt"])
        velocity = self._velocity_status(context_data)
        position_gate_passed = (
            distance <= self.tolerance_xy_m and z_error <= self.tolerance_z_m
        )
        velocity_gate_passed = bool(velocity["velocity_gate_passed"])
        position_within_hysteresis = (
            distance <= self.tolerance_xy_m + self.position_hysteresis_xy_m
            and z_error <= self.tolerance_z_m + self.position_hysteresis_z_m
        )
        velocity_within_hysteresis = self._velocity_within_hysteresis(velocity)
        if position_gate_passed and velocity_gate_passed:
            self.reached_updates += 1
            self.reached_update_action = "increment"
        elif position_within_hysteresis and velocity_within_hysteresis:
            self.reached_updates = max(
                0, self.reached_updates - self.reached_updates_decrement
            )
            self.reached_update_action = (
                "hold" if self.reached_updates_decrement == 0 else "decrement"
            )
        else:
            self.reached_updates = 0
            self.reached_update_action = "reset"
        detail = self._detail(target, yaw_rad, current, distance, z_error, velocity)
        if self.reached_updates >= self.min_hold_updates:
            return ActionResult(done=True, reason="waypoint_reached", detail=detail)
        return ActionResult(
            effects=ActionResult.typed([self._effect(target, yaw_rad, context_data)]),
            reason="goto_active", detail=detail,
        )

    def stop(self) -> None:
        self.stopped = True

    def reset(self) -> None:
        self.started = self.stopped = self._skipped = False
        self.input_kind = "field"
        self.field_x_m = self.field_y_m = self.lat = self.lon = None
        self.altitude_m = self.field_yaw_deg = 0.0
        self.yaw_mode = "hold"
        self.hold_yaw_rad: float | None = None
        self.tolerance_xy_m = self.tolerance_z_m = 0.30
        self.min_hold_updates, self.require_velocity_valid = 1, False
        self.max_horizontal_speed_mps, self.max_vertical_speed_mps = 0.25, 0.15
        self.position_hysteresis_xy_m, self.position_hysteresis_z_m = 0.20, 0.10
        self.horizontal_speed_hysteresis_mps = 0.10
        self.vertical_speed_hysteresis_mps = 0.05
        self.reached_updates_decrement = 1
        self.reached_update_action = "reset"
        self.control_reject_max_updates = 3
        self.control_reject_timeout_s = 1.0
        self.control_reject_count = 0
        self.control_reject_started_monotonic = None
        self.control_reject_reason = None
        self.control_reject_elapsed_s = 0.0
        self.max_duration_s = 180.0
        self.started_monotonic = None
        self.last_now_monotonic: float | None = None
        self.priority, self.key, self.reached_updates = 4, "goto_field_gps", 0

    def observe_dispatch_rejection(
        self, reason: str, *, now_monotonic: float | None = None
    ) -> None:
        """Record a dispatcher control rejection for the next lifecycle tick.

        Telemetry usually reflects a mode loss first, but this also covers the
        short interval where the dispatcher rejects a global goto before that
        state publication reaches the Action context.
        """
        if reason not in {
            "control_not_allowed",
            "flight_mode_not_guided",
            "guided_mode_lost",
        }:
            return
        self._record_control_rejection(
            reason, time.monotonic() if now_monotonic is None else now_monotonic
        )

    def _field_reference(self, context: dict[str, Any]) -> FieldReference | None:
        if not bool(context.get("field_heading_confirmed")) or not bool(context.get("field_origin_gps_confirmed")):
            return None
        status = context.get("field_reference")
        if not isinstance(status, dict) or not (
            bool(status.get("is_confirmed"))
            and bool(status.get("synced_to_runtime"))
            and bool(status.get("is_frozen"))
            and bool(status.get("is_ready_for_field_to_gps"))
        ):
            return None
        heading = self._optional_float(context.get("field_heading_yaw_rad"))
        lat = self._optional_float(context.get("field_origin_lat"))
        lon = self._optional_float(context.get("field_origin_lon"))
        if heading is None or lat is None or lon is None:
            return None
        ref = FieldReference()
        ref.is_confirmed = ref.is_frozen = True
        ref.origin_lat, ref.origin_lon, ref.field_heading_yaw_rad = lat, lon, heading
        return ref

    def _global_target(self, reference: FieldReference) -> dict[str, float]:
        if self.input_kind == "resolved_gps":
            assert self.lat is not None and self.lon is not None
            return {"lat": self.lat, "lon": self.lon, "alt": self.altitude_m}
        assert self.field_x_m is not None and self.field_y_m is not None
        point = field_to_gps(self.field_x_m, self.field_y_m, self.altitude_m, reference)
        return {"lat": point.lat, "lon": point.lon, "alt": point.alt_m}

    def _effect(self, target: dict[str, float], yaw_rad: float | None, context: dict[str, Any]) -> dict[str, Any]:
        ref = context.get("field_reference")
        ref_data = ref if isinstance(ref, dict) else {}
        params: dict[str, Any] = {"lat": target["lat"], "lon": target["lon"], "alt": target["alt"],
                                  "frame": GLOBAL_RELATIVE_ALT_INT}
        if yaw_rad is not None:
            params["yaw"] = yaw_rad
        if isinstance(context.get("field_reference_version"), dict):
            params["field_reference_version"] = dict(context["field_reference_version"])
        return {
            "action_type": "global_goto", "params": params, "input_frame": "field",
            "input_target": {"field_x_m": self.field_x_m, "field_y_m": self.field_y_m,
                             "yaw_mode": self.yaw_mode, "field_yaw_deg": self.field_yaw_deg},
            "global_target": dict(target), "key": self.key, "once": False, "priority": self.priority,
            "field_origin_lat": context.get("field_origin_lat"), "field_origin_lon": context.get("field_origin_lon"),
            "field_heading_yaw_rad": context.get("field_heading_yaw_rad"),
            "field_reference_confirmed": bool(ref_data.get("is_confirmed", True)),
            "field_reference_synced": bool(ref_data.get("synced_to_runtime", True)),
            "field_reference_frozen": bool(ref_data.get("is_frozen", True)),
            "field_gps_transform_ready": bool(ref_data.get("is_ready_for_field_to_gps", True)),
        }

    def _detail(self, target, yaw_rad, current, distance, z_error, velocity=None) -> dict[str, Any]:
        return {
            "input_frame": "field", "input_kind": self.input_kind, "field_x_m": self.field_x_m,
            "field_y_m": self.field_y_m, "yaw_mode": self.yaw_mode,
            "field_yaw_deg": self.field_yaw_deg,
            "hold_yaw_deg": None if self.hold_yaw_rad is None else math.degrees(self.hold_yaw_rad) % 360.0,
            "actual_yaw_deg": None if yaw_rad is None else math.degrees(yaw_rad) % 360.0,
            "actual_yaw_rad": yaw_rad,
            "requested_target": self._target_summary(),
            "active_target": self._target_summary(),
            "global_target": target, "current": current, "distance_xy_m": distance, "z_error_m": z_error,
            "position_gate_passed": (
                distance is not None and z_error is not None
                and distance <= self.tolerance_xy_m and z_error <= self.tolerance_z_m
            ),
            "reached_updates": self.reached_updates, "min_hold_updates": self.min_hold_updates,
            "reached_update_action": self.reached_update_action,
            "position_hysteresis_xy_m": self.position_hysteresis_xy_m,
            "position_hysteresis_z_m": self.position_hysteresis_z_m,
            "horizontal_speed_hysteresis_mps": self.horizontal_speed_hysteresis_mps,
            "vertical_speed_hysteresis_mps": self.vertical_speed_hysteresis_mps,
            "control_reject_count": self.control_reject_count,
            "control_reject_max_updates": self.control_reject_max_updates,
            "control_reject_timeout_s": self.control_reject_timeout_s,
            "control_reject_elapsed_s": self.control_reject_elapsed_s,
            "control_reject_reason": self.control_reject_reason,
            "elapsed_s": self._elapsed_s(),
            "max_duration_s": self.max_duration_s,
            **(velocity or {}),
        }

    def _velocity_status(self, context: dict[str, Any]) -> dict[str, Any]:
        drone = context.get("drone")
        valid = False
        horizontal = vertical = None
        if isinstance(drone, dict) and drone.get("velocity_valid") is True:
            try:
                vx, vy, vz = float(drone["vx"]), float(drone["vy"]), float(drone["vz"])
                if all(math.isfinite(v) for v in (vx, vy, vz)):
                    valid, horizontal, vertical = True, math.hypot(vx, vy), abs(vz)
            except (KeyError, TypeError, ValueError):
                pass
        passed = not self.require_velocity_valid or bool(valid and horizontal is not None and vertical is not None and horizontal <= self.max_horizontal_speed_mps and vertical <= self.max_vertical_speed_mps)
        return {"velocity_required": self.require_velocity_valid, "velocity_valid": valid,
                "horizontal_speed_mps": horizontal, "vertical_speed_mps": vertical, "velocity_gate_passed": passed}

    def _velocity_within_hysteresis(self, velocity: dict[str, Any]) -> bool:
        if not self.require_velocity_valid:
            return True
        horizontal = velocity.get("horizontal_speed_mps")
        vertical = velocity.get("vertical_speed_mps")
        return bool(
            velocity.get("velocity_valid")
            and horizontal is not None
            and vertical is not None
            and float(horizontal) <= self.max_horizontal_speed_mps + self.horizontal_speed_hysteresis_mps
            and float(vertical) <= self.max_vertical_speed_mps + self.vertical_speed_hysteresis_mps
        )

    def _target_summary(self) -> dict[str, Any]:
        target: dict[str, Any] = {"alt_m": self.altitude_m}
        if self.input_kind == "resolved_gps":
            target.update({"lat": self.lat, "lon": self.lon})
        elif self.input_kind == "field":
            target.update({"field_x_m": self.field_x_m, "field_y_m": self.field_y_m})
        return target

    @staticmethod
    def _context_monotonic(context: dict[str, Any]) -> float:
        value = context.get("now_monotonic")
        try:
            result = float(value)
        except (TypeError, ValueError):
            return time.monotonic()
        return result if math.isfinite(result) else time.monotonic()

    @staticmethod
    def _control_rejection(context: dict[str, Any]) -> str | None:
        drone = context.get("drone")
        if not isinstance(drone, dict):
            return None
        if drone.get("control_allowed") is False:
            return "control_not_allowed"
        mode = drone.get("mode")
        if mode is not None and str(mode).strip().upper() != "GUIDED":
            return "guided_mode_lost"
        return None

    def _clear_control_rejection_if_explicitly_allowed(self, context: dict[str, Any]) -> None:
        drone = context.get("drone")
        if not isinstance(drone, dict):
            return
        if drone.get("control_allowed") is True and str(drone.get("mode", "")).strip().upper() == "GUIDED":
            self.control_reject_count = 0
            self.control_reject_started_monotonic = None
            self.control_reject_reason = None
            self.control_reject_elapsed_s = 0.0

    def _record_control_rejection(self, reason: str, now_monotonic: float) -> bool:
        if self.control_reject_started_monotonic is None:
            self.control_reject_started_monotonic = now_monotonic
        self.control_reject_count += 1
        self.control_reject_reason = reason
        self.control_reject_elapsed_s = max(
            0.0, now_monotonic - self.control_reject_started_monotonic
        )
        return self._control_rejection_exceeded(now_monotonic)

    def _control_rejection_exceeded(self, now_monotonic: float) -> bool:
        if self.control_reject_count <= 0:
            return False
        started = self.control_reject_started_monotonic
        elapsed = 0.0 if started is None else max(0.0, now_monotonic - started)
        self.control_reject_elapsed_s = elapsed
        return (
            self.control_reject_count >= self.control_reject_max_updates
            or elapsed >= self.control_reject_timeout_s
        )

    def _elapsed_s(self) -> float:
        if self.started_monotonic is None:
            return 0.0
        now = time.monotonic() if self.last_now_monotonic is None else self.last_now_monotonic
        return max(0.0, now - self.started_monotonic)

    @staticmethod
    def _current_global_position(context: dict[str, Any]) -> dict[str, float] | None:
        drone = context.get("drone")
        if not isinstance(drone, dict) or not bool(drone.get("global_position_valid")):
            return None
        try:
            lat, lon = float(drone["lat"]), float(drone["lon"])
            alt = float(next(drone[name] for name in ("relative_altitude", "relative_altitude_m", "altitude_m") if name in drone))
        except (KeyError, StopIteration, TypeError, ValueError):
            return None
        return {"lat": lat, "lon": lon, "alt": alt} if all(math.isfinite(v) for v in (lat, lon, alt)) else None

    @staticmethod
    def _current_yaw(context: dict[str, Any]) -> float | None:
        drone = context.get("drone")
        if not isinstance(drone, dict) or drone.get("attitude_valid") is not True:
            return None
        try:
            yaw = float(drone["yaw"])
        except (KeyError, TypeError, ValueError):
            return None
        return yaw if math.isfinite(yaw) else None

    @staticmethod
    def _gps_distance_m(lat_a, lon_a, lat_b, lon_b) -> float:
        north = math.radians(lat_b - lat_a) * 6_371_000.0
        east = math.radians(lon_b - lon_a) * 6_371_000.0 * math.cos(math.radians(lat_a))
        return math.hypot(north, east)

    @staticmethod
    def _normalize_yaw(value: float) -> float:
        return (value + math.pi) % (2.0 * math.pi) - math.pi

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @classmethod
    def _finite_float(cls, value: Any, name: str) -> float:
        result = cls._optional_float(value)
        if result is None:
            raise ValueError(f"{name} must be finite")
        return result

    @classmethod
    def _required_float(cls, data: dict[str, Any], name: str, legacy: str | None = None) -> float:
        value = data.get(name, data.get(legacy)) if legacy else data.get(name)
        if value is None:
            raise ValueError(f"{name} is required")
        return cls._finite_float(value, name)

    @classmethod
    def _positive_float(cls, value: Any, name: str) -> float:
        result = cls._finite_float(value, name)
        if result <= 0:
            raise ValueError(f"{name} must be positive")
        return result

    @classmethod
    def _non_negative_float(cls, value: Any, name: str) -> float:
        result = cls._finite_float(value, name)
        if result < 0:
            raise ValueError(f"{name} must be non-negative")
        return result
