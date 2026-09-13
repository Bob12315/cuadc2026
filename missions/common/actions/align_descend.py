"""Continuously align to the scene target nearest the image centre and descend."""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

from contracts.effects import FlightCommand

from .base import ActionModule
from .result import ActionResult


class AlignDescendAction(ActionModule):
    """Align to the nearest scene target until the low-altitude vote succeeds."""

    TIMEOUT_S = 30.0
    ALIGNMENT_WINDOW_FRAMES = 5
    ALIGNMENT_REQUIRED_FRAMES = 3

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
        self.kp_forward = self._non_negative(data.get("kp_forward", 0.3), "kp_forward")
        self.kp_right = self._non_negative(data.get("kp_right", 0.3), "kp_right")
        self.max_vx_mps = self._positive(data.get("max_vx_mps", 0.25), "max_vx_mps")
        self.max_vy_mps = self._positive(data.get("max_vy_mps", 0.25), "max_vy_mps")
        self.vx_sign = self._unit_sign(data.get("vx_sign", -1.0), "vx_sign")
        self.vy_sign = self._unit_sign(data.get("vy_sign", 1.0), "vy_sign")
        self.field_yaw_deg = self._finite(data.get("field_yaw_deg", 0.0), "field_yaw_deg")
        self.desired_yaw_deg = self._optional_finite(data.get("desired_yaw_deg"))
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
        target = self._nearest_scene_target(scene)
        altitude = self._altitude(data)
        if target is None or altitude is None or altitude <= 0.0:
            if altitude is not None and altitude <= self.target_altitude_m:
                self._record_alignment_frame(scene, False)
            reason = "target_not_found" if target is None else "altitude_unavailable"
            return self._holding(reason, yaw_rad=yaw, altitude_m=altitude)

        ex, ey = target["ex"], target["ey"]
        vx = self._clamp(self.vx_sign * self.kp_forward * ey, self.max_vx_mps)
        vy = self._clamp(self.vy_sign * self.kp_right * ex, self.max_vy_mps)
        aligned = abs(ex) <= self.release_deadband_ex and abs(ey) <= self.release_deadband_ey

        if altitude <= self.target_altitude_m:
            vz = 0.0
            self._record_alignment_frame(scene, aligned)
            if len(self.alignment_window) == self.ALIGNMENT_WINDOW_FRAMES and sum(self.alignment_window) >= self.ALIGNMENT_REQUIRED_FRAMES:
                return self._terminal(
                    True,
                    "alignment_confirmed",
                    yaw_rad=yaw,
                    altitude_m=altitude,
                    target=target,
                    aligned=aligned,
                )
            reason = "confirming_alignment"
        else:
            self.alignment_window.clear()
            self.last_counted_frame_id = None
            vz = self.descend_speed_mps
            reason = "align_descending"

        return ActionResult(
            effects=(self._command(vx, vy, vz, yaw),),
            reason=reason,
            detail=self._detail(reason, target, altitude, yaw, vx, vy, vz, aligned),
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
        self.release_deadband_ey = 0.1
        self.kp_forward = 0.3
        self.kp_right = 0.3
        self.max_vx_mps = 0.25
        self.max_vy_mps = 0.25
        self.vx_sign = -1.0
        self.vy_sign = 1.0
        self.field_yaw_deg = 0.0
        self.desired_yaw_deg = None
        self.priority = 5
        self.key = "align_descend"
        self.started_at = 0.0
        self.fixed_yaw_rad: float | None = None
        self.yaw_source = "field_centerline"
        self.alignment_window: deque[bool] = deque(maxlen=self.ALIGNMENT_WINDOW_FRAMES)
        self.last_counted_frame_id: int | None = None

    def _nearest_scene_target(self, scene: object) -> dict[str, float | int] | None:
        if not isinstance(scene, dict):
            return None
        detections = scene.get("detections")
        if not isinstance(detections, list):
            return None

        candidates: list[tuple[float, dict[str, float | int]]] = []
        for detection in detections:
            if not isinstance(detection, dict):
                continue
            ex = self._optional_finite(detection.get("ex"))
            ey = self._optional_finite(detection.get("ey"))
            if ex is None or ey is None:
                continue
            candidate: dict[str, float | int] = {"ex": ex, "ey": ey}
            track_id = self._optional_int(detection.get("track_id"))
            if track_id is not None:
                candidate["track_id"] = track_id
            candidates.append((ex * ex + ey * ey, candidate))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[0])[1]

    def _record_alignment_frame(self, scene: object, aligned: bool) -> None:
        frame_id = None
        if isinstance(scene, dict):
            frame_id = self._optional_int(scene.get("frame_id"))
        if frame_id is not None:
            if frame_id == self.last_counted_frame_id:
                return
            self.last_counted_frame_id = frame_id
        self.alignment_window.append(aligned)

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

    def _command(self, vx: float, vy: float, vz: float, yaw: float) -> FlightCommand:
        return FlightCommand(
            # ActionDispatcher maps yaw_hold_rad to SET_POSITION_TARGET_LOCAL_NED
            # in MAV_FRAME_BODY_NED, with yaw-rate explicitly ignored.
            params={
                "valid": True,
                "active": True,
                "vx_cmd": vx,
                "vy_cmd": vy,
                "vz_cmd": vz,
                "yaw_hold_rad": yaw,
                "control_frame": "MAV_FRAME_BODY_NED",
                "yaw_mode": "absolute_hold",
            },
            key=f"{self.key}_body",
            priority=self.priority,
            once=False,
        )

    def _holding(self, reason: str, *, yaw_rad: float, altitude_m: float | None) -> ActionResult:
        return ActionResult(
            effects=(self._command(0.0, 0.0, 0.0, yaw_rad),),
            reason=reason,
            detail=self._detail(reason, None, altitude_m, yaw_rad, 0.0, 0.0, 0.0, False),
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
    ) -> ActionResult:
        return ActionResult(
            effects=(self._command(0.0, 0.0, 0.0, yaw_rad),),
            done=done,
            failed=not done,
            reason=reason,
            detail=self._detail(reason, target, altitude_m, yaw_rad, 0.0, 0.0, 0.0, aligned),
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
    ) -> dict[str, Any]:
        return {
            "state": reason,
            "enabled": self.enabled,
            "complete_on_timeout": self.complete_on_timeout,
            "target_track_id": None if target is None else target.get("track_id"),
            "ex": None if target is None else target["ex"],
            "ey": None if target is None else target["ey"],
            "altitude_m": altitude,
            "target_altitude_m": self.target_altitude_m,
            "yaw_rad": yaw,
            "yaw_deg": math.degrees(yaw) % 360.0,
            "yaw_source": self.yaw_source,
            "yaw_latched": self.fixed_yaw_rad is not None,
            "control_frame": "MAV_FRAME_BODY_NED",
            "yaw_mode": "absolute_hold",
            "vx_forward_mps": vx,
            "vy_right_mps": vy,
            "vz_down_mps": vz,
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
