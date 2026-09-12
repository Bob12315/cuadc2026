from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from .base import ActionModule
from .result import ActionResult


_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class _AltitudeSample:
    value_m: float
    source: str


class TakeoffAction(ActionModule):
    """Arm, command takeoff, then lock the fixed FIELD-centre heading.

    FIELD +Y is the agreed field-centre direction.  ``yaw_mode=hold`` remains
    an explicit compatibility option for callers that deliberately do not want
    the final heading-alignment phase.
    """

    def __init__(self) -> None:
        self.reset()

    def start(self, params: dict[str, Any] | None = None) -> None:
        data = params or {}
        raw_mode = data.get("mode", "GUIDED")
        if not isinstance(raw_mode, str):
            raise ValueError("mode must be a non-empty string")
        mode = raw_mode.strip().upper()
        if not mode:
            raise ValueError("mode must be a non-empty string")

        altitude_m = float(data.get("altitude_m", data.get("target_alt_m", 3.0)))
        altitude_tolerance_m = float(data.get("altitude_tolerance_m", 0.3))
        max_updates = int(data.get("max_updates", 120))
        max_duration_s = self._optional_positive_seconds(data.get("max_duration_s"))
        if altitude_m <= 0.0:
            raise ValueError("altitude_m must be positive")
        if altitude_tolerance_m <= 0.0:
            raise ValueError("altitude_tolerance_m must be positive")
        if max_updates < 1:
            raise ValueError("max_updates must be at least 1")

        self.mode = mode
        self.altitude_m = altitude_m
        self.altitude_tolerance_m = altitude_tolerance_m
        self.require_armed = self._parse_bool(data.get("require_armed", True), "require_armed")
        self.max_updates = max_updates
        self.max_duration_s = max_duration_s
        self.yaw_mode = str(data.get("yaw_mode") or "field_heading").strip().lower()
        if self.yaw_mode not in {"field_heading", "hold"}:
            raise ValueError("yaw_mode must be 'field_heading' or 'hold'")
        raw_takeoff_yaw = data.get("takeoff_yaw_deg")
        self.takeoff_yaw_deg = (
            None if raw_takeoff_yaw is None
            else self._finite_required(raw_takeoff_yaw, "takeoff_yaw_deg")
        )
        # ``field_yaw_deg`` / ``yaw_deg`` were introduced before the absolute
        # takeoff-yaw API.  Retain them only as a relative FIELD +Y offset for
        # older callers; new callers use takeoff_yaw_deg or omit it entirely.
        self.field_yaw_offset_deg = self._finite_required(
            data.get("field_yaw_deg", data.get("yaw_deg", 0.0)), "field_yaw_deg"
        )
        self.yaw_source = (
            "explicit" if self.takeoff_yaw_deg is not None
            else "legacy_field_heading_offset"
            if any(name in data for name in ("field_yaw_deg", "yaw_deg"))
            else "field_centerline"
        )
        self.yaw_tolerance_deg = self._positive_finite(
            data.get("yaw_tolerance_deg", 5.0), "yaw_tolerance_deg"
        )
        if self.yaw_tolerance_deg > 180.0:
            raise ValueError("yaw_tolerance_deg must be <= 180")
        self.yaw_min_hold_updates = max(1, int(data.get("yaw_min_hold_updates", 2)))
        self.yaw_timeout_s = self._positive_finite(
            data.get("yaw_timeout_s", 12.0), "yaw_timeout_s"
        )
        self.yaw_speed_deg_s = self._positive_finite(
            data.get("yaw_speed_deg_s", 20.0), "yaw_speed_deg_s"
        )
        self.started_monotonic_s = time.monotonic()
        self.priority = int(data.get("priority", 2))
        self.arm_priority = int(data.get("arm_priority", 1))
        self.mode_priority = int(data.get("mode_priority", 2))
        self.key = str(data.get("key") or "takeoff")

        self.phase = "set_mode"
        self.started = True
        self.stopped = False
        self.done = False
        self.failed = False
        self.failure_reason = ""
        self.update_count = 0
        self.mode_sent = False
        self.arm_sent = False
        self.takeoff_sent = False
        self.yaw_sent = False
        self.yaw_reached_updates = 0
        self.yaw_target_rad: float | None = None
        self.yaw_alignment_started_monotonic_s: float | None = None
        self.last_detail = self._detail()

    def update(self, context: dict[str, Any] | None = None) -> ActionResult:
        if not self.started:
            return ActionResult(failed=True, reason="action_not_started")
        if self.stopped:
            return ActionResult(done=True, reason="stopped", detail=self._detail())
        if self.done:
            return ActionResult(done=True, reason="takeoff_done", detail=dict(self.last_detail))

        if self.yaw_mode == "field_heading" and self.yaw_target_rad is None:
            target_yaw = self._resolve_takeoff_yaw(context or {})
            if target_yaw is None:
                self.phase = "failed"
                self.failed = True
                self.failure_reason = "field_heading_not_ready"
                detail = self._detail(context=context)
                self.last_detail = detail
                return ActionResult(
                    failed=True, reason="field_heading_not_ready", detail=detail
                )
            self.yaw_target_rad = target_yaw
            _LOG.info(
                "takeoff yaw source: %s (target=%.1fdeg)",
                self.yaw_source,
                math.degrees(target_yaw) % 360.0,
            )

        self.update_count += 1
        context_data = context or {}
        altitude = self._current_altitude(context_data)
        # Mode switching and arming are asynchronous MAVLink state changes.
        # Their confirmation can be delayed by a slow SITL clock, so the
        # altitude timeout must start only after the takeoff effect is issued.
        # max_updates remains the bounded pre-takeoff guard.
        timed_out = self.update_count > self.max_updates
        if self.max_duration_s is not None and self.takeoff_started_monotonic_s is not None:
            timed_out = time.monotonic() - self.takeoff_started_monotonic_s >= self.max_duration_s
        if timed_out:
            self.phase = "failed"
            self.failed = True
            self.failure_reason = "takeoff_timeout"
            detail = self._detail(altitude, context=context_data)
            self.last_detail = detail
            return ActionResult(failed=True, reason="takeoff_timeout", detail=detail)

        if self.phase == "set_mode":
            current_mode = self._context_mode(context_data)
            if current_mode == self.mode:
                self.phase = "arm" if self.require_armed else "takeoff"
                return ActionResult(
                    reason="mode_confirmed",
                    detail=self._detail(altitude, phase="mode_confirmed", context=context_data),
                )
            action = {
                "action_type": "set_mode",
                "params": {"mode": self.mode},
                "key": f"{self.key}_set_mode",
                "once": True,
                "priority": self.mode_priority,
            }
            self.mode_sent = True
            detail = self._detail(altitude, phase="set_mode", context=context_data)
            self.last_detail = detail
            return ActionResult(effects=ActionResult.typed([action]), reason="set_mode_sent", detail=detail)

        if self.phase == "arm":
            if self._context_armed(context_data) is True:
                self.phase = "takeoff"
                return ActionResult(
                    reason="armed_confirmed",
                    detail=self._detail(altitude, phase="armed_confirmed", context=context_data),
                )
            action = {
                "action_type": "arm",
                "params": {},
                "key": f"{self.key}_arm",
                "once": True,
                "priority": self.arm_priority,
            }
            self.arm_sent = True
            detail = self._detail(altitude, phase="arm", context=context_data)
            self.last_detail = detail
            return ActionResult(effects=ActionResult.typed([action]), reason="arm_sent", detail=detail)

        if self.phase == "takeoff":
            return self._takeoff_result(altitude, context_data)

        if self.phase == "wait_altitude":
            return self._wait_for_takeoff_completion(altitude, context_data)

        return ActionResult(failed=True, reason="invalid_takeoff_phase", detail=self._detail(altitude, context=context_data))

    def stop(self) -> None:
        self.stopped = True

    def reset(self) -> None:
        self.phase = "idle"
        self.started = False
        self.stopped = False
        self.done = False
        self.failed = False
        self.update_count = 0
        self.failure_reason = ""
        self.mode_sent = False
        self.arm_sent = False
        self.takeoff_sent = False
        self.yaw_sent = False
        self.yaw_reached_updates = 0
        self.yaw_target_rad: float | None = None
        self.yaw_alignment_started_monotonic_s: float | None = None
        self.mode = "GUIDED"
        self.altitude_m = 3.0
        self.altitude_tolerance_m = 0.3
        self.require_armed = True
        self.max_updates = 120
        self.max_duration_s: float | None = None
        self.yaw_mode = "field_heading"
        self.takeoff_yaw_deg: float | None = None
        self.field_yaw_offset_deg = 0.0
        self.yaw_source = "field_centerline"
        self.yaw_tolerance_deg = 5.0
        self.yaw_min_hold_updates = 2
        self.yaw_timeout_s = 12.0
        self.yaw_speed_deg_s = 20.0
        self.started_monotonic_s: float | None = None
        self.takeoff_started_monotonic_s: float | None = None
        self.priority = 2
        self.arm_priority = 1
        self.mode_priority = 2
        self.key = "takeoff"
        self.last_detail: dict[str, Any] = {}

    def _takeoff_result(self, altitude: _AltitudeSample | None, context: dict[str, Any] | None = None) -> ActionResult:
        action = {
            "action_type": "takeoff",
            "params": {"altitude_m": self.altitude_m},
            "key": f"{self.key}_takeoff",
            "once": True,
            "priority": self.priority,
        }
        self.takeoff_sent = True
        self.takeoff_started_monotonic_s = time.monotonic()
        detail = self._detail(altitude, phase="takeoff", context=context)
        self.last_detail = detail
        self.phase = "wait_altitude"
        _LOG.info("guided takeoff start: alt=%.2fm", self.altitude_m)
        return ActionResult(effects=ActionResult.typed([action]), reason="takeoff_sent", detail=detail)

    def _wait_for_takeoff_completion(
        self,
        altitude: _AltitudeSample | None,
        context: dict[str, Any],
    ) -> ActionResult:
        """Hold one resolved yaw throughout the climb and finish at altitude."""
        if self.yaw_mode == "field_heading":
            yaw_result = self._update_yaw_lock(altitude, context)
            if yaw_result is not None:
                return yaw_result

        if altitude is None:
            detail = self._detail(None, context=context)
            self.last_detail = detail
            return ActionResult(
                reason=(
                    "waiting_for_field_heading_yaw"
                    if self.yaw_mode == "field_heading" and self.yaw_reached_updates < self.yaw_min_hold_updates
                    else "waiting_for_altitude"
                ),
                detail=detail,
            )

        reached_altitude = altitude.value_m >= self.altitude_m - self.altitude_tolerance_m
        yaw_reached = (
            self.yaw_mode == "hold"
            or self.yaw_reached_updates >= self.yaw_min_hold_updates
        )
        detail = self._detail(altitude, reached=reached_altitude, context=context)
        self.last_detail = detail
        if reached_altitude and yaw_reached:
            self.done = True
            self.phase = "done"
            reason = (
                "takeoff_altitude_reached"
                if self.yaw_mode == "hold"
                else "takeoff_field_heading_reached"
            )
            _LOG.info(
                "guided takeoff complete: alt=%.2fm yaw=%.1fdeg source=%s",
                altitude.value_m,
                math.degrees(self.yaw_target_rad) % 360.0 if self.yaw_target_rad is not None else float("nan"),
                self.yaw_source,
            )
            return ActionResult(done=True, reason=reason, detail=detail)
        if not reached_altitude and self.yaw_mode == "field_heading" and not yaw_reached:
            return ActionResult(reason="waiting_for_takeoff_altitude_and_field_heading", detail=detail)
        if not reached_altitude:
            return ActionResult(reason="waiting_for_takeoff_altitude", detail=detail)
        return ActionResult(reason="waiting_for_field_heading_yaw", detail=detail)

    def _update_yaw_lock(
        self,
        altitude: _AltitudeSample | None,
        context: dict[str, Any],
    ) -> ActionResult | None:
        """Emit one absolute CONDITION_YAW after TAKEOFF, then observe it."""
        target_yaw = self.yaw_target_rad
        if target_yaw is None:
            self.phase = "failed"
            self.failed = True
            self.failure_reason = "field_heading_not_ready"
            detail = self._detail(altitude, context=context)
            self.last_detail = detail
            return ActionResult(failed=True, reason="field_heading_not_ready", detail=detail)

        if not self.yaw_sent:
            self.yaw_alignment_started_monotonic_s = time.monotonic()
            self.yaw_sent = True
            target_yaw_deg = math.degrees(target_yaw) % 360.0
            action = {
                "action_type": "condition_yaw",
                "params": {
                    "yaw_deg": target_yaw_deg,
                    "yaw_speed_deg_s": self.yaw_speed_deg_s,
                    "direction": 0,
                    "relative": False,
                },
                "key": f"{self.key}_field_heading",
                "once": True,
                "priority": self.priority,
            }
            _LOG.info(
                "takeoff yaw lock: target=%.1fdeg speed=%.1fdeg/s source=%s",
                target_yaw_deg,
                self.yaw_speed_deg_s,
                self.yaw_source,
            )
            detail = self._detail(altitude, context=context)
            self.last_detail = detail
            return ActionResult(
                effects=ActionResult.typed([action]),
                reason="field_heading_yaw_sent",
                detail=detail,
            )

        now = time.monotonic()
        assert self.yaw_alignment_started_monotonic_s is not None
        if now - self.yaw_alignment_started_monotonic_s >= self.yaw_timeout_s:
            self.phase = "failed"
            self.failed = True
            self.failure_reason = "field_heading_yaw_timeout"
            detail = self._detail(altitude, context=context)
            self.last_detail = detail
            return ActionResult(
                failed=True, reason="field_heading_yaw_timeout", detail=detail
            )

        current_yaw = self._current_yaw_rad(context)
        if current_yaw is not None and self._yaw_error_deg(current_yaw, target_yaw) <= self.yaw_tolerance_deg:
            self.yaw_reached_updates += 1
        else:
            self.yaw_reached_updates = 0
        return None

    def _current_altitude(self, context: dict[str, Any]) -> _AltitudeSample | None:
        for name in ("relative_altitude", "relative_altitude_m", "altitude_m"):
            sample = self._float_sample(context, name, name)
            if sample is not None:
                return sample

        sample = self._negative_z_sample(context, "local_z")
        if sample is not None:
            return sample

        local_position = context.get("local_position")
        if isinstance(local_position, dict):
            sample = self._negative_z_sample(local_position, "local_position.z")
            if sample is not None:
                return sample

        drone = context.get("drone")
        if isinstance(drone, dict):
            for name in ("relative_altitude", "relative_altitude_m", "altitude_m"):
                sample = self._float_sample(drone, name, f"drone.{name}")
                if sample is not None:
                    return sample
            sample = self._negative_z_sample(drone, "drone.local_z")
            if sample is not None:
                return sample
            local_position = drone.get("local_position")
            if isinstance(local_position, dict):
                sample = self._negative_z_sample(local_position, "drone.local_position.z")
                if sample is not None:
                    return sample

        vehicle = context.get("vehicle")
        if isinstance(vehicle, dict):
            for name in ("relative_altitude", "relative_altitude_m"):
                sample = self._float_sample(vehicle, name, f"vehicle.{name}")
                if sample is not None:
                    return sample
            sample = self._negative_z_sample(vehicle, "vehicle.local_z")
            if sample is not None:
                return sample

        return None

    @staticmethod
    def _context_mode(context: dict[str, Any]) -> str | None:
        value = context.get("mode")
        drone = context.get("drone")
        if value is None and isinstance(drone, dict):
            value = drone.get("mode")
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip().upper()

    @staticmethod
    def _context_armed(context: dict[str, Any]) -> bool | None:
        value = context.get("armed")
        drone = context.get("drone")
        if value is None and isinstance(drone, dict):
            value = drone.get("armed")
        return value if isinstance(value, bool) else None

    def _float_sample(self, data: dict[str, Any], name: str, source: str) -> _AltitudeSample | None:
        if name not in data:
            return None
        try:
            value = float(data[name])
        except (TypeError, ValueError):
            return None
        return _AltitudeSample(max(0.0, value), source)

    def _negative_z_sample(self, data: dict[str, Any], source: str) -> _AltitudeSample | None:
        value = None
        for name in ("local_z", "z"):
            if name in data:
                try:
                    value = float(data[name])
                except (TypeError, ValueError):
                    value = None
                break
        if value is not None and value < 0.0:
            return _AltitudeSample(max(0.0, -value), source)
        return None

    def _detail(
        self,
        altitude: _AltitudeSample | None = None,
        *,
        phase: str | None = None,
        reached: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current_altitude_m = None if altitude is None else altitude.value_m
        if reached is None:
            reached = (
                current_altitude_m is not None
                and current_altitude_m >= self.altitude_m - self.altitude_tolerance_m
            )
        context_data = context or {}
        detail: dict[str, Any] = {
            "phase": phase or self.phase,
            "mode": self.mode,
            "target_altitude_m": self.altitude_m,
            "altitude_tolerance_m": self.altitude_tolerance_m,
            "current_altitude_m": current_altitude_m,
            "altitude_source": "" if altitude is None else altitude.source,
            "reached": reached,
            "update_count": self.update_count,
            "max_updates": self.max_updates,
            "max_duration_s": self.max_duration_s,
            "mode_sent": self.mode_sent,
            "arm_sent": self.arm_sent,
            "takeoff_sent": self.takeoff_sent,
            "yaw_mode": self.yaw_mode,
            "takeoff_yaw_deg": self.takeoff_yaw_deg,
            "field_yaw_offset_deg": self.field_yaw_offset_deg,
            "takeoff_yaw_source": self.yaw_source,
            "resolved_takeoff_yaw_deg": None if self.yaw_target_rad is None else math.degrees(self.yaw_target_rad) % 360.0,
            "current_yaw_deg": self._current_yaw_deg(context_data),
            "yaw_error_deg": self._current_yaw_error_deg(context_data),
            "yaw_tolerance_deg": self.yaw_tolerance_deg,
            "yaw_reached_updates": self.yaw_reached_updates,
            "yaw_min_hold_updates": self.yaw_min_hold_updates,
            "yaw_sent": self.yaw_sent,
            "yaw_timeout_s": self.yaw_timeout_s,
            "yaw_alignment_elapsed_s": self._yaw_alignment_elapsed_s(),
        }
        for name in (
            "field_heading_confirmed",
            "field_heading_yaw_rad",
            "field_heading_source",
            "pre_arm_yaw_rad",
        ):
            if name in context_data:
                detail[name] = context_data[name]
        return detail

    @staticmethod
    def _optional_positive_seconds(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_duration_s must be finite and > 0") from exc
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("max_duration_s must be finite and > 0")
        return value

    def _current_yaw_rad(self, context: dict[str, Any]) -> float | None:
        drone = context.get("drone")
        if isinstance(drone, dict) and bool(drone.get("attitude_valid", False)):
            yaw = self._finite_float(drone.get("yaw"))
            if yaw is not None:
                return yaw
        yaw = self._finite_float(context.get("yaw"))
        if yaw is not None:
            return yaw
        return None

    def _resolve_takeoff_yaw(self, context: dict[str, Any]) -> float | None:
        """Resolve explicit north yaw, otherwise the canonical FIELD +Y heading."""
        if self.takeoff_yaw_deg is not None:
            return self._normalize_yaw(math.radians(self.takeoff_yaw_deg))
        if context.get("field_heading_confirmed") is not True:
            return None
        heading = self._finite_float(context.get("field_heading_yaw_rad"))
        if heading is None:
            return None
        return self._normalize_yaw(
            heading + math.radians(self.field_yaw_offset_deg)
        )

    def _current_yaw_deg(self, context: dict[str, Any]) -> float | None:
        yaw = self._current_yaw_rad(context)
        return None if yaw is None else math.degrees(self._normalize_yaw(yaw)) % 360.0

    def _current_yaw_error_deg(self, context: dict[str, Any]) -> float | None:
        if self.yaw_target_rad is None:
            return None
        yaw = self._current_yaw_rad(context)
        return None if yaw is None else self._yaw_error_deg(yaw, self.yaw_target_rad)

    def _yaw_alignment_elapsed_s(self) -> float | None:
        if self.yaw_alignment_started_monotonic_s is None:
            return None
        return max(0.0, time.monotonic() - self.yaw_alignment_started_monotonic_s)

    @staticmethod
    def _normalize_yaw(yaw_rad: float) -> float:
        return float(yaw_rad) % math.tau

    @staticmethod
    def _yaw_error_deg(current_yaw_rad: float, target_yaw_rad: float) -> float:
        error_rad = (float(current_yaw_rad) - float(target_yaw_rad) + math.pi) % math.tau - math.pi
        return abs(math.degrees(error_rad))

    def _attitude_valid(self, context: dict[str, Any]) -> bool:
        drone = context.get("drone")
        if isinstance(drone, dict):
            return bool(drone.get("attitude_valid", False))
        return False

    def _field_heading_drone(self, context: dict[str, Any], yaw: float) -> dict[str, Any] | None:
        drone = context.get("drone")
        if not isinstance(drone, dict) or not bool(drone.get("local_position_valid", False)):
            return None
        local_x = self._finite_float(drone.get("local_x"))
        local_y = self._finite_float(drone.get("local_y"))
        local_z = self._finite_float(drone.get("local_z"))
        if local_x is None or local_y is None or local_z is None:
            return None
        return {
            "yaw": yaw,
            "local_position_valid": True,
            "local_x": local_x,
            "local_y": local_y,
            "local_z": local_z,
        }

    @staticmethod
    def _parse_bool(value: Any, name: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError(f"{name} must be a bool")

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @classmethod
    def _finite_required(cls, value: Any, name: str) -> float:
        result = cls._finite_float(value)
        if result is None:
            raise ValueError(f"{name} must be finite")
        return result

    @classmethod
    def _positive_finite(cls, value: Any, name: str) -> float:
        result = cls._finite_required(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive")
        return result
