from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_M = 6371000.0
MIN_GPS_BASELINE_M = 5.0
RECOMMENDED_GPS_BASELINE_M = 10.0
WGS84_POLE_COS_EPS = 1e-9


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------

class OriginSource(str, Enum):
    """Supported schema-v3 origin source."""

    RUNTIME_CURRENT_GPS = "runtime_current_gps"


class HeadingSource(str, Enum):
    """Supported schema-v3 heading source."""

    RUNTIME_FORWARD_MARKER = "runtime_forward_marker"
    RUNTIME_RTK_HEADING = "runtime_rtk_heading"


# ---------------------------------------------------------------------------
# exception
# ---------------------------------------------------------------------------

class FieldReferenceError(Exception):
    """Raised when a FieldReference operation is invalid."""


# ---------------------------------------------------------------------------
# FieldReference
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FieldReference:
    """Pure-data field reference with validation and lifecycle methods.

    Coordinates use the conventions from ``docs/developer/coordinate_frames.md``:

    FIELD uses ``field_x_m`` (+X = right) and ``field_y_m`` (+Y = forward),
    and converts only to/from global GPS in the formal schema-v3 path.
    """

    is_confirmed: bool = False
    is_frozen: bool = False

    origin_source: Optional[str] = None
    heading_source: Optional[str] = None

    # Runtime GPS origin.
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None

    # GPS forward marker (for gps_two_point heading)
    forward_marker_lat: Optional[float] = None
    forward_marker_lon: Optional[float] = None

    # FIELD +Y heading in LOCAL_NED, normalized to (-pi, pi]
    field_heading_yaw_rad: Optional[float] = None

    # Unix timestamp of last successful confirm()
    confirmed_at_s: Optional[float] = None

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_yaw(yaw_rad: float) -> float:
        """Normalize *yaw_rad* to (-pi, pi].

        Maps ``-pi`` to ``+pi`` so the range is truly half-open at the
        negative end.
        """
        yaw = math.atan2(math.sin(yaw_rad), math.cos(yaw_rad))
        # atan2(sin(-pi), cos(-pi)) may return -pi due to floating-point
        # artifacts; force it to +pi for a clean (-pi, pi] range.
        if yaw <= -math.pi + 1e-15:
            return math.pi
        return yaw

    def _guard_confirmed(self) -> None:
        if not self.is_confirmed:
            raise FieldReferenceError("FieldReference is not confirmed")

    def _guard_not_frozen(self) -> None:
        if self.is_frozen:
            raise FieldReferenceError(
                "FieldReference is frozen; reset before modifying"
            )

    # ------------------------------------------------------------------
    # readiness
    # ------------------------------------------------------------------

    def is_ready_for_field_to_gps(self) -> bool:
        """True when ready for FIELD → GLOBAL GPS transforms.

        Requires confirmed reference, WGS84 origin, and heading.
        Does NOT require LOCAL_NED origin or forward marker.
        Defensively handles non-finite / non-numeric field values.
        """
        if not self.is_confirmed:
            return False

        origin_lat = self.origin_lat
        origin_lon = self.origin_lon
        heading = self.field_heading_yaw_rad

        if any(v is None for v in (origin_lat, origin_lon, heading)):
            return False

        if any(isinstance(v, bool) for v in (origin_lat, origin_lon, heading)):
            return False

        try:
            lat = float(origin_lat)
            lon = float(origin_lon)
            hdg = float(heading)
        except (TypeError, ValueError):
            return False

        if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(hdg)):
            return False

        if not -90.0 <= lat <= 90.0:
            return False

        if not -180.0 <= lon <= 180.0:
            return False

        if abs(math.cos(math.radians(lat))) <= WGS84_POLE_COS_EPS:
            return False

        return True

    def is_ready(self) -> bool:
        """Return schema-v3 FIELD ↔ GPS readiness."""
        return self.is_ready_for_field_to_gps()

    def freeze(self) -> None:
        """Freeze the confirmed reference so origin/heading cannot be
        accidentally changed during mission execution."""
        self._guard_confirmed()
        self.is_frozen = True

    def reset(self) -> None:
        """Clear all fields back to the unconfirmed, unfrozen state."""
        self.is_confirmed = False
        self.is_frozen = False
        self.origin_source = None
        self.heading_source = None
        self.origin_lat = None
        self.origin_lon = None
        self.forward_marker_lat = None
        self.forward_marker_lon = None
        self.field_heading_yaw_rad = None
        self.confirmed_at_s = None


# ---------------------------------------------------------------------------
# module-level GPS utilities (small-range ENU approximation)
# ---------------------------------------------------------------------------


def normalize_longitude_deg(longitude_deg: float) -> float:
    """Normalize a finite longitude to the canonical range [-180, 180)."""
    if not (
        isinstance(longitude_deg, (int, float))
        and not isinstance(longitude_deg, bool)
        and math.isfinite(float(longitude_deg))
    ):
        raise FieldReferenceError(
            f"longitude must be a finite number, got {longitude_deg!r}"
        )
    normalized = (float(longitude_deg) + 180.0) % 360.0 - 180.0
    return -180.0 if normalized == 180.0 else normalized


def shortest_longitude_delta_deg(
    from_lon_deg: float,
    to_lon_deg: float,
) -> float:
    """Return the shortest signed longitude delta from *from* to *to*."""
    if not (
        isinstance(from_lon_deg, (int, float))
        and not isinstance(from_lon_deg, bool)
        and math.isfinite(float(from_lon_deg))
    ):
        raise FieldReferenceError(
            f"from longitude must be a finite number, got {from_lon_deg!r}"
        )
    if not (
        isinstance(to_lon_deg, (int, float))
        and not isinstance(to_lon_deg, bool)
        and math.isfinite(float(to_lon_deg))
    ):
        raise FieldReferenceError(
            f"to longitude must be a finite number, got {to_lon_deg!r}"
        )
    return normalize_longitude_deg(float(to_lon_deg) - float(from_lon_deg))


def circular_median_longitude_deg(
    longitudes_deg: Iterable[float],
) -> float:
    """Return a deterministic median longitude without a dateline seam."""
    try:
        values = tuple(longitudes_deg)
    except Exception as exc:
        raise FieldReferenceError(
            f"longitudes must be an iterable of finite numbers: {exc}"
        ) from exc
    if not values:
        raise FieldReferenceError("at least one longitude is required")
    parsed: list[float] = []
    for index, value in enumerate(values):
        if not (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            raise FieldReferenceError(
                f"longitude[{index}] must be a finite number, got {value!r}"
            )
        value_f = float(value)
        if not -180.0 <= value_f <= 180.0:
            raise FieldReferenceError(
                f"longitude[{index}] {value_f} out of range [-180, 180]"
            )
        parsed.append(value_f)
    reference = normalize_longitude_deg(parsed[0])
    try:
        median_delta = statistics.median(
            shortest_longitude_delta_deg(reference, value)
            for value in parsed
        )
    except Exception as exc:
        raise FieldReferenceError(
            f"failed to compute circular longitude median: {exc}"
        ) from exc
    return normalize_longitude_deg(reference + median_delta)


def validate_wgs84_lat_lon(
    lat: object,
    lon: object,
    *,
    reject_pole: bool,
) -> tuple[float, float]:
    """Validate and return numeric WGS84 latitude/longitude values."""
    for name, value in (("latitude", lat), ("longitude", lon)):
        if not (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            raise FieldReferenceError(
                f"{name} must be a finite number, got {value!r}"
            )
    lat_f = float(lat)
    lon_f = float(lon)
    if not -90.0 <= lat_f <= 90.0:
        raise FieldReferenceError(f"latitude {lat_f} out of range [-90, 90]")
    if not -180.0 <= lon_f <= 180.0:
        raise FieldReferenceError(f"longitude {lon_f} out of range [-180, 180]")
    if reject_pole and abs(math.cos(math.radians(lat_f))) <= WGS84_POLE_COS_EPS:
        raise FieldReferenceError("latitude is too close to a WGS84 pole")
    return lat_f, lon_f

def gps_enu_deltas(
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
) -> Tuple[float, float]:
    """Return (d_north_m, d_east_m) from *a* to *b* using a small-range
    ENU approximation.  Inputs are decimal degrees."""
    lat_a_f, lon_a_f = validate_wgs84_lat_lon(
        lat_a, lon_a, reject_pole=True
    )
    lat_b_f, lon_b_f = validate_wgs84_lat_lon(
        lat_b, lon_b, reject_pole=True
    )
    lat_a_rad = math.radians(lat_a_f)
    lat_b_rad = math.radians(lat_b_f)
    d_north = (lat_b_rad - lat_a_rad) * EARTH_RADIUS_M
    d_east = (
        math.radians(shortest_longitude_delta_deg(lon_a_f, lon_b_f))
        * EARTH_RADIUS_M
        * math.cos(lat_a_rad)
    )
    return d_north, d_east


def _gps_distance_m(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    """Great-circle distance approximation (ENU flat-earth for small ranges)."""
    d_north, d_east = gps_enu_deltas(lat_a, lon_a, lat_b, lon_b)
    return math.hypot(d_north, d_east)


def _gps_bearing_rad(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    """Bearing from *a* to *b* in radians (north = 0, east = +pi/2)."""
    d_north, d_east = gps_enu_deltas(lat_a, lon_a, lat_b, lon_b)
    return math.atan2(d_east, d_north)
