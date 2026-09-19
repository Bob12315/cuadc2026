"""Lock a scene target by track ID and image-position continuity, then descend."""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

from contracts.effects import ConditionYaw, FlightCommand

from .base import ActionModule
from .result import ActionResult
class AlignDescendAction(ActionModule):
    """Align to one tracked scene target until the low-altitude vote succeeds."""

    TIMEOUT_S = 30.0
    ALIGNMENT_WINDOW_FRAMES = 5
    ALIGNMENT_REQUIRED_FRAMES = 2
    def __init__(self) -> None:
        self.reset()
    def start(self, params: dict[str, Any] | None = None) -> None:
        data = params or {}
        self.enabled = self._boolean(data.get("enabled", True), "enabled")
        self.complete_on_timeout = self._boolean(
            data.get("complete_on_timeout", False), "complete_on_timeout"
        )
        self.target_altitude_m = self._positive(data.get("target_altitude_m", 1.2), "target_altitude_m")
        self.descend_speed_mps = self._non_negative(data.get("descend_speed_mps", 0.2), "descend_speed_mps")
        self.release_deadband_ex = self._positive(data.get("release_deadband_ex", 0.1), "release_deadband_ex")
        self.release_deadband_ey = self._positive(data.get("release_deadband_ey", 0.1), "release_deadband_ey")
        self.release_target_ex = self._finite(data.get("release_target_ex", 0.0), "release_target_ex")
        self.release_target_ey = self._finite(data.get("release_target_ey", 0.0), "release_target_ey")
        self.release_max_speed_mps = self._positive(
            data.get("release_max_speed_mps", 0.1), "release_max_speed_mps"
        )
        self.final_settle_time_s = self._non_negative(
            data.get("final_settle_time_s", 0.0), "final_settle_time_s"
        )
        self.kp_forward = self._non_negative(data.get("kp_forward", 0.3), "kp_forward")
        self.kp_right = self._non_negative(data.get("kp_right", 0.3), "kp_right")
        self.final_kp_forward = self._non_negative(
            data.get("final_kp_forward", self.kp_forward), "final_kp_forward"
        )
        self.final_kp_right = self._non_negative(
            data.get("final_kp_right", self.kp_right), "final_kp_right"
        )
        self.ki_forward = self._non_negative(data.get("ki_forward", 0.0), "ki_forward")
        self.ki_right = self._non_negative(data.get("ki_right", 0.0), "ki_right")
        self.integral_limit = self._non_negative(data.get("integral_limit", 0.25), "integral_limit")
        self.max_vx_mps = self._positive(data.get("max_vx_mps", 0.25), "max_vx_mps")
        self.max_vy_mps = self._positive(data.get("max_vy_mps", 0.25), "max_vy_mps")
        self.final_max_vx_mps = self._positive(
            data.get("final_max_vx_mps", self.max_vx_mps), "final_max_vx_mps"
        )
        self.final_max_vy_mps = self._positive(
            data.get("final_max_vy_mps", self.max_vy_mps), "final_max_vy_mps"
        )
        self.vx_sign = self._unit_sign(data.get("vx_sign", -1.0), "vx_sign")
        self.vy_sign = self._unit_sign(data.get("vy_sign", 1.0), "vy_sign")
        self.field_yaw_deg = self._finite(data.get("field_yaw_deg", 0.0), "field_yaw_deg")
        self.desired_yaw_deg = self._optional_finite(data.get("desired_yaw_deg"))
        self.yaw_speed_deg_s = self._positive(data.get("yaw_speed_deg_s", 20.0), "yaw_speed_deg_s")
        self.priority = int(data.get("priority", 5))
        self.key = str(data.get("key") or "align_descend").strip() or "align_descend"
        self.started_at = time.monotonic()
        # Resolve from the field reference at the first Action update, then
        # keep exactly that absolute heading for this entire Action.  In
        # particular, target-loss holding must not alter the yaw setpoint.
        self.fixed_yaw_rad: float | None = None
        self.yaw_source = "explicit" if self.desired_yaw_deg is not None else "field_centerline"
        self.alignment_window.clear()
        self.last_counted_frame_id = None
        self.final_altitude_latched = False
        self.final_settle_until: float | None = None
        self.final_settle_remaining_s = 0.0
        self.locked_target_track_id = None
        self.locked_target_ex = None
        self.locked_target_ey = None
        self.target_lock_state = "waiting_for_target"
        self._reset_integral()
        self.started = True
        self.stopped = False
    def update(self, context: dict[str, Any] | None = None) -> ActionResult:
        if not self.started:
            return ActionResult(failed=True, reason="action_not_started")

        data = context or {}
        yaw = self._fixed_yaw_rad(data)
        if self.stopped:
            return self._terminal(True, "stopped", yaw_rad=yaw)
        if not self.enabled:
            return self._terminal(True, "alignment_skipped", yaw_rad=yaw)
        if time.monotonic() - self.started_at >= self.TIMEOUT_S:
            return self._terminal(
                self.complete_on_timeout,
                "alignment_timeout_accepted" if self.complete_on_timeout else "align_descend_timeout",
                yaw_rad=yaw,
            )

        scene = data.get("scene")
        altitude = self._altitude(data)
        # The vehicle's height controller may briefly rise after it starts
        # holding the final height.  Once reached, keep the final-height mode
        # latched for this action instead of reissuing a descent command.
        if (
            not self.final_altitude_latched
            and altitude is not None
            and 0.0 < altitude <= self.target_altitude_m
        ):
            self.final_altitude_latched = True
            if self.final_settle_time_s > 0.0:
                self.final_settle_until = time.monotonic() + self.final_settle_time_s
            self._clear_integral()
        if self._final_altitude_settling():
            return self._holding(
                "final_altitude_settling", yaw_rad=yaw, altitude_m=altitude
            )
        target = self._locked_scene_target(scene)
        speed_mps = self._speed_mps(data)
        if target is None or altitude is None or altitude <= 0.0:
            self._reset_integral()
            if self.final_altitude_latched:
                self._record_alignment_frame(scene, False)
            reason = "target_not_found" if target is None else "altitude_unavailable"
            return self._holding(
                reason, yaw_rad=yaw, altitude_m=altitude, speed_mps=speed_mps
            )

        ex, ey = target["ex"], target["ey"]
        release_offset_active = self.final_altitude_latched
        release_target_ex = self.release_target_ex if release_offset_active else 0.0
        release_target_ey = self.release_target_ey if release_offset_active else 0.0
        alignment_ex = ex - release_target_ex
        alignment_ey = ey - release_target_ey
        self._update_integral(scene, alignment_ex, alignment_ey)
        kp_forward = self.final_kp_forward if release_offset_active else self.kp_forward
        kp_right = self.final_kp_right if release_offset_active else self.kp_right
        max_vx_mps = self.final_max_vx_mps if release_offset_active else self.max_vx_mps
        max_vy_mps = self.final_max_vy_mps if release_offset_active else self.max_vy_mps
        vx = self._clamp(
            self.vx_sign * (kp_forward * alignment_ey + self.ki_forward * self.integral_forward),
            max_vx_mps,
        )
        vy = self._clamp(
            self.vy_sign * (kp_right * alignment_ex + self.ki_right * self.integral_right),
            max_vy_mps,
        )
        aligned = abs(alignment_ex) <= self.release_deadband_ex and abs(alignment_ey) <= self.release_deadband_ey

        if self.final_altitude_latched:
            vz = 0.0
            self._record_alignment_frame(scene, aligned)
            alignment_confirmed = (
                len(self.alignment_window) == self.ALIGNMENT_WINDOW_FRAMES
                and sum(self.alignment_window) >= self.ALIGNMENT_REQUIRED_FRAMES
            )
            speed_within_release_limit = (
                speed_mps is not None and speed_mps < self.release_max_speed_mps
            )
            if alignment_confirmed and speed_within_release_limit:
                return self._terminal(
                    True,
                    "alignment_confirmed",
                    yaw_rad=yaw,
                    altitude_m=altitude,
                    target=target,
                    aligned=aligned,
                    speed_mps=speed_mps,
                )
            reason = (
                "waiting_for_release_speed"
                if alignment_confirmed else "confirming_alignment"
            )
        else:
            self.alignment_window.clear()
            self.last_counted_frame_id = None
            vz = self.descend_speed_mps
            reason = "align_descending"

        return ActionResult(
            effects=self._effects(vx, vy, vz, yaw),
            reason=reason,
            detail=self._detail(
                reason, target, altitude, yaw, vx, vy, vz, aligned, speed_mps
            ),
        )
    def stop(self) -> None:
        self.stopped = True

    def reset(self) -> None:
        self.started = False
        self.stopped = False
        self.enabled = True
        self.complete_on_timeout = False
        self.target_altitude_m = 1.2
        self.descend_speed_mps = 0.2
        self.release_deadband_ex = 0.1
        self.release_target_ex = 0.0
        self.release_target_ey = 0.0
        self.release_max_speed_mps = 0.1
        self.final_settle_time_s = 0.0
        self.release_deadband_ey = 0.1
        self.kp_forward = 0.3
        self.kp_right = 0.3
        self.final_kp_forward = 0.3
        self.final_kp_right = 0.3
        self.ki_forward = 0.0
        self.ki_right = 0.0
        self.integral_limit = 0.25
        self.max_vx_mps = 0.25
        self.max_vy_mps = 0.25
        self.final_max_vx_mps = 0.25
        self.final_max_vy_mps = 0.25
        self.vx_sign = -1.0
        self.vy_sign = 1.0
        self.field_yaw_deg = 0.0
        self.desired_yaw_deg = None
        self.yaw_speed_deg_s = 20.0
        self.priority = 5
        self.key = "align_descend"
        self.started_at = 0.0
        self.fixed_yaw_rad: float | None = None
        self.yaw_source = "field_centerline"
        self.alignment_window: deque[bool] = deque(maxlen=self.ALIGNMENT_WINDOW_FRAMES)
        self.last_counted_frame_id: int | None = None
        self.final_altitude_latched = False
        self.final_settle_until: float | None = None
        self.final_settle_remaining_s = 0.0
        self.locked_target_track_id: int | None = None
        self.locked_target_ex: float | None = None
        self.locked_target_ey: float | None = None
        self.target_lock_state = "not_started"
        self.integral_forward = 0.0
        self.integral_right = 0.0
        self.last_integral_frame_id: int | None = None
        self.last_integral_at = 0.0

    def _final_altitude_settling(self) -> bool:
        """Hold all velocity commands briefly after first reaching final height."""
        if self.final_settle_until is None:
            self.final_settle_remaining_s = 0.0
            return False
        self.final_settle_remaining_s = max(
            0.0, self.final_settle_until - time.monotonic()
        )
        return self.final_settle_remaining_s > 0.0

    def _locked_scene_target(self, scene: object) -> dict[str, float | int] | None:
        candidates = self._scene_targets(scene)
        if not candidates:
            self.target_lock_state = (
                "locked_target_not_visible"
                if self.locked_target_track_id is not None
                else "target_not_found"
            )
            return None

        if self.locked_target_track_id is not None:
            for _, candidate in candidates:
                if candidate.get("track_id") == self.locked_target_track_id:
                    self.target_lock_state = "locked"
                    self._remember_locked_target(candidate)
                    return candidate

        had_locked_position = (
            self.locked_target_ex is not None and self.locked_target_ey is not None
        )
        if had_locked_position:
            _, target = min(
                candidates,
                key=lambda item: (
                    (float(item[1]["ex"]) - self.locked_target_ex) ** 2
                    + (float(item[1]["ey"]) - self.locked_target_ey) ** 2
                ),
            )
            self.target_lock_state = "relocked_nearest_position"
        else:
            _, target = min(candidates, key=lambda item: item[0])
            self.target_lock_state = "acquired"
        self._remember_locked_target(target)
        return target

    def _remember_locked_target(self, target: dict[str, float | int]) -> None:
        self.locked_target_ex = float(target["ex"])
        self.locked_target_ey = float(target["ey"])
        track_id = target.get("track_id")
        self.locked_target_track_id = None if track_id is None else int(track_id)

    @classmethod
    def _scene_targets(cls, scene: object) -> list[tuple[float, dict[str, float | int]]]:
        if not isinstance(scene, dict):
            return []
        detections = scene.get("detections")
        if not isinstance(detections, list):
            return []

        candidates: list[tuple[float, dict[str, float | int]]] = []
        for detection in detections:
            if not isinstance(detection, dict):
                continue
            ex = cls._optional_finite(detection.get("ex"))
            ey = cls._optional_finite(detection.get("ey"))
            if ex is None or ey is None:
                continue
            candidate: dict[str, float | int] = {"ex": ex, "ey": ey}
            track_id = cls._optional_int(detection.get("track_id"))
            if track_id is not None:
                candidate["track_id"] = track_id
            candidates.append((ex * ex + ey * ey, candidate))
        return candidates

    def _record_alignment_frame(self, scene: object, aligned: bool) -> None:
        frame_id = None
        if isinstance(scene, dict):
            frame_id = self._optional_int(scene.get("frame_id"))
        if frame_id is not None:
            if frame_id == self.last_counted_frame_id:
                return
            self.last_counted_frame_id = frame_id
        self.alignment_window.append(aligned)

    def _reset_integral(self) -> None:
        self._clear_integral()
        self.last_integral_at = time.monotonic()

    def _clear_integral(self) -> None:
        self.integral_forward = 0.0
        self.integral_right = 0.0
        self.last_integral_frame_id = None

    def _update_integral(self, scene: object, error_ex: float, error_ey: float) -> None:
        """Accumulate fresh visual errors only, with bounded, sign-safe PI memory."""
        frame_id = None
        if isinstance(scene, dict):
            frame_id = self._optional_int(scene.get("frame_id"))
        if frame_id is not None and frame_id == self.last_integral_frame_id:
            return

        now = time.monotonic()
        # Vision usually arrives much faster than the controller loop.  A capped
        # delta avoids a scheduling pause creating a velocity jump on recovery.
        dt = min(max(now - self.last_integral_at, 0.0), 0.2)
        if self.integral_forward * error_ey < 0.0:
            self.integral_forward = 0.0
        if self.integral_right * error_ex < 0.0:
            self.integral_right = 0.0
        self.integral_forward = self._clamp(
            self.integral_forward + error_ey * dt, self.integral_limit
        )
        self.integral_right = self._clamp(
            self.integral_right + error_ex * dt, self.integral_limit
        )
        self.last_integral_frame_id = frame_id
        self.last_integral_at = now

    def _fixed_yaw_rad(self, data: dict[str, Any]) -> float:
        if self.fixed_yaw_rad is None:
            self.fixed_yaw_rad = self._resolve_yaw_rad(data)
        return self.fixed_yaw_rad

    def _resolve_yaw_rad(self, data: dict[str, Any]) -> float:
        if self.desired_yaw_deg is not None:
            return self._normalize(math.radians(self.desired_yaw_deg))
        heading = self._optional_finite(data.get("field_heading_yaw_rad")) or 0.0
        return self._normalize(heading + math.radians(self.field_yaw_deg))

    @staticmethod
    def _altitude(data: dict[str, Any]) -> float | None:
        drone = data.get("drone")
        source = drone if isinstance(drone, dict) else data
        for name in ("relative_altitude", "relative_altitude_m", "altitude_m"):
            value = AlignDescendAction._optional_finite(source.get(name))
            if value is not None and value >= 0.0:
                return value
        local_z = AlignDescendAction._optional_finite(source.get("local_z"))
        return -local_z if local_z is not None and local_z <= 0.0 else None

    @staticmethod
    def _speed_mps(data: dict[str, Any]) -> float | None:
        """Return the measured 3D EKF velocity magnitude when it is complete."""
        drone = data.get("drone")
        source = drone if isinstance(drone, dict) else data
        components = [
            AlignDescendAction._optional_finite(source.get(name))
            for name in ("vx", "vy", "vz")
        ]
        if any(component is None for component in components):
            return None
        return math.sqrt(sum(float(component) ** 2 for component in components))

    def _effects(self, vx: float, vy: float, vz: float, yaw: float) -> tuple[ConditionYaw | FlightCommand, ...]:
        # ArduCopter interprets the yaw field of MAV_FRAME_BODY_NED as a
        # *relative* heading.  Repeating an absolute field yaw in that field
        # therefore creates a continuously advancing yaw target.  Lock the
        # absolute heading once with the existing CONDITION_YAW path, then
        # keep all continuous alignment motion yaw-free in BODY_NED.
        return (
            ConditionYaw(
                params={
                    "yaw_deg": math.degrees(yaw) % 360.0,
                    "yaw_speed_deg_s": self.yaw_speed_deg_s,
                    "direction": 0,
                    "relative": False,
                },
                key=f"{self.key}_yaw_lock",
                priority=self.priority,
                once=True,
            ),
            self._command(vx, vy, vz),
        )

    def _command(self, vx: float, vy: float, vz: float) -> FlightCommand:
        return FlightCommand(
            # BODY_NED yaw is relative, so the absolute heading stays on the one-shot
            # CONDITION_YAW command.  Keep yaw ignored but make yaw_rate=0 valid (mask 1479).
            # This prevents the velocity controller from selecting a movement-facing yaw.
            params={
                "valid": True,
                "active": True,
                "vx_cmd": vx,
                "vy_cmd": vy,
                "yaw_rate_rad_s": 0.0,
                "vz_cmd": vz,
                "control_frame": "MAV_FRAME_BODY_NED",
                "yaw_mode": "condition_yaw_absolute",
            },
            key=f"{self.key}_body",
            priority=self.priority,
            once=False,
        )

    def _holding(
        self,
        reason: str,
        *,
        yaw_rad: float,
        altitude_m: float | None,
        speed_mps: float | None = None,
    ) -> ActionResult:
        return ActionResult(
            effects=self._effects(0.0, 0.0, 0.0, yaw_rad),
            reason=reason,
            detail=self._detail(
                reason, None, altitude_m, yaw_rad, 0.0, 0.0, 0.0, False, speed_mps
            ),
        )

    def _terminal(
        self,
        done: bool,
        reason: str,
        *,
        yaw_rad: float,
        altitude_m: float | None = None,
        target: dict[str, float | int] | None = None,
        aligned: bool = False,
        speed_mps: float | None = None,
    ) -> ActionResult:
        return ActionResult(
            effects=self._effects(0.0, 0.0, 0.0, yaw_rad),
            done=done,
            failed=not done,
            reason=reason,
            detail=self._detail(
                reason, target, altitude_m, yaw_rad, 0.0, 0.0, 0.0, aligned, speed_mps
            ),
        )

    def _detail(
        self,
        reason: str,
        target: dict[str, float | int] | None,
        altitude: float | None,
        yaw: float,
        vx: float,
        vy: float,
        vz: float,
        aligned: bool,
        speed_mps: float | None,
    ) -> dict[str, Any]:
        release_offset_active = self.final_altitude_latched
        release_target_ex = self.release_target_ex if release_offset_active else 0.0
        release_target_ey = self.release_target_ey if release_offset_active else 0.0
        alignment_error_ex = None if target is None else target["ex"] - release_target_ex
        alignment_error_ey = None if target is None else target["ey"] - release_target_ey
        active_max_vx_mps = self.final_max_vx_mps if release_offset_active else self.max_vx_mps
        active_max_vy_mps = self.final_max_vy_mps if release_offset_active else self.max_vy_mps
        active_kp_forward = self.final_kp_forward if release_offset_active else self.kp_forward
        active_kp_right = self.final_kp_right if release_offset_active else self.kp_right
        return {
            "state": reason,
            "release_offset_active": release_offset_active,
            "speed_control_phase": "final_altitude" if release_offset_active else "descending",
            "final_altitude_latched": self.final_altitude_latched,
            "final_settle_time_s": self.final_settle_time_s,
            "final_settling": self.final_settle_remaining_s > 0.0,
            "final_settle_remaining_s": self.final_settle_remaining_s,
            "release_target_ex": release_target_ex,
            "release_target_ey": release_target_ey,
            "release_max_speed_mps": self.release_max_speed_mps,
            "measured_speed_mps": speed_mps,
            "within_release_speed_limit": (
                speed_mps is not None and speed_mps < self.release_max_speed_mps
            ),
            "alignment_error_ex": alignment_error_ex,
            "alignment_error_ey": alignment_error_ey,
            "enabled": self.enabled,
            "complete_on_timeout": self.complete_on_timeout,
            "target_lock_enabled": True,
            "target_lock_mode": "track_id_or_nearest_previous_position",
            "locked_target_track_id": self.locked_target_track_id,
            "locked_target_ex": self.locked_target_ex,
            "locked_target_ey": self.locked_target_ey,
            "target_lock_state": self.target_lock_state,
            "target_track_id": None if target is None else target.get("track_id"),
            "ex": None if target is None else target["ex"],
            "ey": None if target is None else target["ey"],
            "altitude_m": altitude,
            "target_altitude_m": self.target_altitude_m,
            "yaw_rad": yaw,
            "yaw_deg": math.degrees(yaw) % 360.0,
            "yaw_source": self.yaw_source,
            "yaw_latched": self.fixed_yaw_rad is not None,
            "yaw_speed_deg_s": self.yaw_speed_deg_s,
            "yaw_lock_effect_key": f"{self.key}_yaw_lock",
            "yaw_lock_relative": False,
            "control_frame": "MAV_FRAME_BODY_NED",
            "yaw_mode": "condition_yaw_absolute",
            "vx_forward_mps": vx,
            "vy_right_mps": vy,
            "vz_down_mps": vz,
            "kp_forward": self.kp_forward,
            "kp_right": self.kp_right,
            "final_kp_forward": self.final_kp_forward,
            "final_kp_right": self.final_kp_right,
            "active_kp_forward": active_kp_forward,
            "active_kp_right": active_kp_right,
            "ki_forward": self.ki_forward,
            "ki_right": self.ki_right,
            "integral_forward": self.integral_forward,
            "integral_right": self.integral_right,
            "integral_limit": self.integral_limit,
            "max_vx_mps": self.max_vx_mps,
            "max_vy_mps": self.max_vy_mps,
            "final_max_vx_mps": self.final_max_vx_mps,
            "final_max_vy_mps": self.final_max_vy_mps,
            "active_max_vx_mps": active_max_vx_mps,
            "active_max_vy_mps": active_max_vy_mps,
            "within_release_deadband": aligned,
            "alignment_window": list(self.alignment_window),
            "alignment_hits": sum(self.alignment_window),
        }

    @staticmethod
    def _clamp(value: float, limit: float) -> float:
        return max(-limit, min(limit, value))

    @staticmethod
    def _normalize(value: float) -> float:
        return math.atan2(math.sin(value), math.cos(value))

    @staticmethod
    def _optional_finite(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @staticmethod
    def _boolean(value: Any, name: str) -> bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"{name} must be a bool")

    @classmethod
    def _finite(cls, value: Any, name: str) -> float:
        result = cls._optional_finite(value)
        if result is None:
            raise ValueError(f"{name} must be finite")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be > 0")
        return result

    @classmethod
    def _non_negative(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be >= 0")
        return result

    @classmethod
    def _unit_sign(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result not in {-1.0, 1.0}:
            raise ValueError(f"{name} must be -1 or 1")
        return result

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None
