"""Capture-time GPS target projection.

Pure algorithm: converts a single-frame detection (ex, ey) into a
WGS84 GPS target using the drone's GPS position, yaw, and altitude
at the moment of capture.

No global state, no MAVLink, no field reference.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# camera config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GpsProjectionCamera:
    """Fixed camera parameters for GPS projection (competition spec)."""

    fov_x_deg: float = 51.3
    fov_y_deg: float = 39.6
    image_x_sign: float = 1.0
    image_y_sign: float = -1.0
    min_altitude_m: float = 0.1
    ground_distance_scale: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 < self.fov_x_deg < 180.0:
            raise ValueError("fov_x_deg must be in (0, 180)")
        if not 0.0 < self.fov_y_deg < 180.0:
            raise ValueError("fov_y_deg must be in (0, 180)")
        if self.image_x_sign not in {1.0, -1.0}:
            raise ValueError("image_x_sign must be ±1")
        if self.image_y_sign not in {1.0, -1.0}:
            raise ValueError("image_y_sign must be ±1")
        if self.min_altitude_m <= 0.0:
            raise ValueError("min_altitude_m must be > 0")
        if not 0.0 < self.ground_distance_scale <= 1.0:
            raise ValueError("ground_distance_scale must be in (0, 1]")


# ---------------------------------------------------------------------------
# raw estimate DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GpsRawEstimate:
    """A single raw GPS target estimate from one detection."""

    lat: float
    lon: float
    east_offset_m: float
    north_offset_m: float
    capture_drone_lat: float
    capture_drone_lon: float
    capture_yaw_rad: float
    capture_relative_altitude_m: float
    ex: float
    ey: float
    class_name: str
    confidence: Optional[float] = None
    track_id: Optional[int] = None
    frame_id: Optional[int] = None
    timestamp: Optional[float] = None
    source_waypoint: Optional[str] = None


# ---------------------------------------------------------------------------
# projector
# ---------------------------------------------------------------------------


class GpsTargetProjector:
    """Project a single detection to a WGS84 GPS target.

    Conventions:
    - yaw north = 0, yaw east = +π/2
    - BODY forward/right (forward = +Y, right = +X in body frame)
    - ENU east/north
    - FIELD +Y forward, +X right
    """

    # Earth radius in metres (WGS84 mean)
    _EARTH_RADIUS_M: float = 6371000.0

    def __init__(self, camera: GpsProjectionCamera | None = None) -> None:
        self.camera = camera or GpsProjectionCamera()

    def project(
        self,
        *,
        drone_lat: float,
        drone_lon: float,
        drone_yaw_rad: float,
        relative_altitude_m: float,
        ex: float,
        ey: float,
        class_name: str = "",
        confidence: Optional[float] = None,
        track_id: Optional[int] = None,
        frame_id: Optional[int] = None,
        timestamp: Optional[float] = None,
        source_waypoint: Optional[str] = None,
    ) -> GpsRawEstimate:
        """Project a single detection to WGS84 GPS.

        Args:
            drone_lat, drone_lon: Capture-time drone GPS
            drone_yaw_rad: Capture-time drone yaw
            relative_altitude_m: Capture-time relative altitude (> 0)
            ex, ey: Normalised detection error (-1..1 in image space)

        Returns:
            GpsRawEstimate with lat/lon + diagnostic fields

        Raises:
            GpsProjectionError: on invalid input
        """
        # ── input validation ──────────────────────────────────────────
        _validate_finite("drone_lat", drone_lat, -90.0, 90.0)
        _validate_finite("drone_lon", drone_lon, -180.0, 180.0)
        _validate_finite("drone_yaw_rad", drone_yaw_rad)
        _validate_finite("relative_altitude_m", relative_altitude_m,
                         low=self.camera.min_altitude_m)
        _validate_finite("ex", ex)
        _validate_finite("ey", ey)

        # ── camera projection ─────────────────────────────────────────
        half_fov_x = math.radians(self.camera.fov_x_deg) / 2.0
        half_fov_y = math.radians(self.camera.fov_y_deg) / 2.0

        # ``ex``/``ey`` are normalized image-plane coordinates, not a linear
        # fraction of the optical angle.  Use the pinhole projection so that
        # a normalized image coordinate maps back to its ground-ray angle.
        # The former linear-angle approximation increasingly underestimated
        # offsets away from the image centre for the 2 rad Gazebo camera.
        angle_x = math.atan(ex * math.tan(half_fov_x))
        angle_y = math.atan(ey * math.tan(half_fov_y))

        # body-frame offsets (forward/right, metres on ground plane)
        body_right_m = (
            self.camera.ground_distance_scale
            * self.camera.image_x_sign * relative_altitude_m * math.tan(angle_x)
        )
        body_forward_m = (
            self.camera.ground_distance_scale
            * self.camera.image_y_sign * relative_altitude_m * math.tan(angle_y)
        )

        # ── rotate body → ENU east/north ──────────────────────────────
        cos_yaw = math.cos(drone_yaw_rad)
        sin_yaw = math.sin(drone_yaw_rad)
        # BODY forward (+Y) → ENU north (+N), BODY right (+X) → ENU east (+E)
        east_offset_m = body_forward_m * sin_yaw + body_right_m * cos_yaw
        north_offset_m = body_forward_m * cos_yaw - body_right_m * sin_yaw

        # ── GPS deltas ────────────────────────────────────────────────
        lat_rad = math.radians(drone_lat)
        d_lat_deg = math.degrees(north_offset_m / self._EARTH_RADIUS_M)
        d_lon_deg = math.degrees(
            east_offset_m / (self._EARTH_RADIUS_M * math.cos(lat_rad))
        )

        target_lat = drone_lat + d_lat_deg
        target_lon = drone_lon + d_lon_deg

        # normalise longitude
        if target_lon > 180.0:
            target_lon -= 360.0
        elif target_lon < -180.0:
            target_lon += 360.0

        _validate_finite("target_lat (computed)", target_lat, -90.0, 90.0)
        _validate_finite("target_lon (computed)", target_lon, -180.0, 180.0)

        return GpsRawEstimate(
            lat=target_lat,
            lon=target_lon,
            east_offset_m=east_offset_m,
            north_offset_m=north_offset_m,
            capture_drone_lat=drone_lat,
            capture_drone_lon=drone_lon,
            capture_yaw_rad=drone_yaw_rad,
            capture_relative_altitude_m=relative_altitude_m,
            ex=ex,
            ey=ey,
            class_name=class_name,
            confidence=confidence,
            track_id=track_id,
            frame_id=frame_id,
            timestamp=timestamp,
            source_waypoint=source_waypoint,
        )

    def project_detection(
        self,
        detection: Dict[str, Any],
        capture_snapshot: Dict[str, Any],
        *,
        image_width: int | float | None = None,
        image_height: int | float | None = None,
    ) -> GpsRawEstimate:
        """Project a YOLO detection dict + capture telemetry snapshot → GPS.

        The *capture_snapshot* must contain capture-time fields:
        ``drone_lat``, ``drone_lon``, ``drone_yaw_rad``, ``relative_altitude_m``.
        """
        # extract ex/ey from detection
        if "ex" in detection and "ey" in detection:
            ex = float(detection["ex"])
            ey = float(detection["ey"])
        elif "cx" in detection and "cy" in detection:
            w = _require_positive(image_width, "image_width")
            h = _require_positive(image_height, "image_height")
            cx = float(detection["cx"])
            cy = float(detection["cy"])
            ex = (cx - w / 2.0) / (w / 2.0)
            ey = (cy - h / 2.0) / (h / 2.0)
        else:
            raise GpsProjectionError("detection must have ex/ey or cx/cy+image size")

        confidence = detection.get("confidence")
        if confidence is not None:
            confidence = float(confidence)

        return self.project(
            drone_lat=float(capture_snapshot["drone_lat"]),
            drone_lon=float(capture_snapshot["drone_lon"]),
            drone_yaw_rad=float(capture_snapshot["drone_yaw_rad"]),
            relative_altitude_m=float(capture_snapshot["relative_altitude_m"]),
            ex=ex,
            ey=ey,
            class_name=str(detection.get("class_name") or ""),
            confidence=confidence,
            track_id=_optional_int(detection.get("track_id")),
            frame_id=_optional_int(detection.get("frame_id")),
            timestamp=_optional_float(detection.get("timestamp")),
            source_waypoint=str(capture_snapshot.get("source_waypoint") or ""),
        )


# ---------------------------------------------------------------------------
# error
# ---------------------------------------------------------------------------


class GpsProjectionError(ValueError):
    """Invalid input for GPS projection."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _validate_finite(
    name: str,
    value: float,
    low: float = float("-inf"),
    high: float = float("inf"),
) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value))):
        raise GpsProjectionError(f"{name} must be a finite number, got {value!r}")
    f = float(value)
    if f < low or f > high:
        raise GpsProjectionError(f"{name} must be in [{low}, {high}], got {f}")


def _require_positive(
    value: int | float | None,
    name: str,
) -> float:
    if value is None:
        raise GpsProjectionError(f"{name} is required")
    f = float(value)
    if f <= 0.0 or not math.isfinite(f):
        raise GpsProjectionError(f"{name} must be positive, got {value!r}")
    return f


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None
