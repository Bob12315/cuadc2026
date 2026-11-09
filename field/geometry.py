"""Runtime field geometry — pure math layer.

Schema v3 ``FieldProfile`` + dynamic origin GPS A → FIELD +Y heading,
baseline, scan waypoints, area boundaries, and home point.

Pure functions only: no global state, no telemetry, no flight commands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

from .coordinates import field_to_gps_from_origin
from .profile import (
    BindingPolicy,
    DropScanConfig,
    FieldGeometry,
    FieldProfile,
    ForwardMarker,
    validate_field_profile,
)
from .models import (
    FieldReferenceError,
    gps_enu_deltas,
    normalize_longitude_deg,
    validate_wgs84_lat_lon,
)


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------


class RuntimeFieldGeometryError(ValueError):
    """Invalid input or geometry for runtime field generation."""


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeFieldPoint:
    """A single point in both FIELD-metre and GLOBAL GPS coordinates."""

    name: str
    field_x_m: float
    field_y_m: float
    altitude_m: float
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class RuntimeFieldGeometry:
    """Complete runtime field layout derived from Schema v3 + dynamic origin."""

    profile_id: str

    origin_lat: float
    origin_lon: float

    forward_marker_lat: float
    forward_marker_lon: float

    field_heading_yaw_rad: float
    field_heading_deg: float
    baseline_m: float

    home: RuntimeFieldPoint
    forward_marker: RuntimeFieldPoint

    drop_scan_waypoints: Tuple[RuntimeFieldPoint, ...]
    recon_scan_waypoints: Tuple[RuntimeFieldPoint, ...]
    drop_area_corners: Tuple[RuntimeFieldPoint, ...]
    recce_area_corners: Tuple[RuntimeFieldPoint, ...]

    warnings: Tuple[str, ...]


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


def build_runtime_field_geometry(
    profile: FieldProfile,
    *,
    origin_lat: float,
    origin_lon: float,
) -> RuntimeFieldGeometry:
    """Build a complete runtime field layout from a validated Schema v3
    ``FieldProfile`` and a dynamic origin GPS position.

    Pure function — does not mutate *profile*.
    """
    _validate_inputs(profile, origin_lat, origin_lon)

    marker: ForwardMarker = profile.forward_marker  # type: ignore[assignment]
    fg: FieldGeometry = profile.field_geometry
    bp: BindingPolicy = profile.binding_policy

    origin_lat, origin_lon = validate_wgs84_lat_lon(
        origin_lat, origin_lon, reject_pole=True
    )
    marker_lat, marker_lon = validate_wgs84_lat_lon(
        marker.lat, marker.lon, reject_pole=True
    )
    origin_lon = normalize_longitude_deg(origin_lon)
    marker_lon = normalize_longitude_deg(marker_lon)
    try:
        d_north, d_east = gps_enu_deltas(
            origin_lat, origin_lon, marker_lat, marker_lon
        )
    except FieldReferenceError as exc:
        raise RuntimeFieldGeometryError(f"invalid A→B WGS84 geometry: {exc}") from exc
    baseline_m = math.hypot(d_north, d_east)
    heading_rad = _normalize_yaw(math.atan2(d_east, d_north))
    heading_deg = math.degrees(heading_rad)

    warnings: list[str] = []

    # baseline policy
    if baseline_m < bp.min_baseline_m and not math.isclose(
        baseline_m, bp.min_baseline_m, rel_tol=0.0, abs_tol=1e-6
    ):
        raise RuntimeFieldGeometryError(
            f"A→B baseline {baseline_m:.1f} m is below minimum "
            f"{bp.min_baseline_m} m"
        )
    if baseline_m < bp.warn_baseline_below_m and not math.isclose(
        baseline_m, bp.warn_baseline_below_m, rel_tol=0.0, abs_tol=1e-6
    ):
        warnings.append(
            f"A→B baseline {baseline_m:.1f} m is below warning threshold "
            f"{bp.warn_baseline_below_m} m (minimum {bp.min_baseline_m} m)"
        )

    # collect validation warnings
    val_diag = validate_field_profile(profile)
    warnings.extend(val_diag.warnings)

    home = RuntimeFieldPoint(
        name="HOME",
        field_x_m=0.0,
        field_y_m=0.0,
        altitude_m=0.0,
        lat=origin_lat,
        lon=origin_lon,
    )

    fwd = RuntimeFieldPoint(
        name=marker.name,
        field_x_m=0.0,
        field_y_m=baseline_m,
        altitude_m=0.0,
        lat=marker_lat,
        lon=marker_lon,
    )

    try:
        scan_points = _build_scan_points(
            profile.drop_scan, origin_lat, origin_lon, heading_rad
        )
        drop_corners = _build_area_corners(
            "D", fg.lane_half_width_m,
            fg.drop_area_y_min, fg.drop_area_y_max,
            origin_lat, origin_lon, heading_rad,
        )
        recce_corners = _build_area_corners(
            "R", fg.lane_half_width_m,
            fg.recce_area_y_min, fg.recce_area_y_max,
            origin_lat, origin_lon, heading_rad,
        )
        recon_scan_points = (
            _build_scan_points(profile.recon_scan, origin_lat, origin_lon, heading_rad, prefix="RECON_SCAN")
            if profile.recon_scan is not None and profile.recon_scan.waypoints
            else _build_recon_scan_points(recce_corners, scan_points)
        )
    except FieldReferenceError as exc:
        raise RuntimeFieldGeometryError(
            f"runtime FIELD→GPS projection failed: {exc}"
        ) from exc

    geometry = RuntimeFieldGeometry(
        profile_id=profile.profile_id,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        forward_marker_lat=marker_lat,
        forward_marker_lon=marker_lon,
        field_heading_yaw_rad=heading_rad,
        field_heading_deg=heading_deg,
        baseline_m=baseline_m,
        home=home,
        forward_marker=fwd,
        drop_scan_waypoints=tuple(scan_points),
        recon_scan_waypoints=tuple(recon_scan_points),
        drop_area_corners=tuple(drop_corners),
        recce_area_corners=tuple(recce_corners),
        warnings=tuple(warnings),
    )
    for point in (
        geometry.home,
        geometry.forward_marker,
        *geometry.drop_scan_waypoints,
        *geometry.recon_scan_waypoints,
        *geometry.drop_area_corners,
        *geometry.recce_area_corners,
    ):
        _validate_generated_point(point)
    return geometry


def build_runtime_field_geometry_from_heading(
    profile: FieldProfile,
    *,
    origin_lat: float,
    origin_lon: float,
    heading_rad: float,
) -> RuntimeFieldGeometry:
    """Build runtime field geometry from a direct heading (RTK dual-antenna).

    Unlike ``build_runtime_field_geometry`` which derives heading from A→B,
    this function takes the heading directly.  A synthetic forward marker is
    placed 50 m along the heading for data-structure compatibility.

    Pure function — does not mutate *profile*.
    """
    if not isinstance(profile, FieldProfile):
        raise RuntimeFieldGeometryError("profile must be a FieldProfile instance")
    if profile.schema_version != 3:
        raise RuntimeFieldGeometryError(
            f"only schema v3 supported, got v{profile.schema_version}"
        )
    if profile.drop_scan is None:
        raise RuntimeFieldGeometryError("profile has no drop_scan")
    if profile.drop_scan.waypoints is None or len(profile.drop_scan.waypoints) != 4:
        raise RuntimeFieldGeometryError("profile.drop_scan must have exactly 4 waypoints")

    if not (
        isinstance(heading_rad, (int, float))
        and not isinstance(heading_rad, bool)
        and math.isfinite(float(heading_rad))
    ):
        raise RuntimeFieldGeometryError(
            f"heading_rad must be a finite number, got {heading_rad!r}"
        )

    try:
        origin_lat, origin_lon = validate_wgs84_lat_lon(
            origin_lat, origin_lon, reject_pole=True
        )
    except FieldReferenceError as exc:
        raise RuntimeFieldGeometryError(f"invalid origin: {exc}") from exc

    origin_lon = normalize_longitude_deg(origin_lon)
    heading_rad = _normalize_yaw(float(heading_rad))
    heading_deg = math.degrees(heading_rad)

    # Synthetic forward marker 50 m along heading for structural compatibility
    synthetic_baseline_m = 50.0
    fwd_gps = field_to_gps_from_origin(
        0.0, synthetic_baseline_m, 0.0,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        field_heading_yaw_rad=heading_rad,
    )
    marker_lat = fwd_gps.lat
    marker_lon = fwd_gps.lon

    fg: FieldGeometry = profile.field_geometry
    warnings: list[str] = []

    val_diag = validate_field_profile(profile)
    warnings.extend(val_diag.warnings)

    home = RuntimeFieldPoint(
        name="HOME",
        field_x_m=0.0,
        field_y_m=0.0,
        altitude_m=0.0,
        lat=origin_lat,
        lon=origin_lon,
    )

    fwd = RuntimeFieldPoint(
        name="RTK_HEADING_MARKER",
        field_x_m=0.0,
        field_y_m=synthetic_baseline_m,
        altitude_m=0.0,
        lat=marker_lat,
        lon=marker_lon,
    )

    try:
        scan_points = _build_scan_points(
            profile.drop_scan, origin_lat, origin_lon, heading_rad
        )
        drop_corners = _build_area_corners(
            "D", fg.lane_half_width_m,
            fg.drop_area_y_min, fg.drop_area_y_max,
            origin_lat, origin_lon, heading_rad,
        )
        recce_corners = _build_area_corners(
            "R", fg.lane_half_width_m,
            fg.recce_area_y_min, fg.recce_area_y_max,
            origin_lat, origin_lon, heading_rad,
        )
        recon_scan_points = (
            _build_scan_points(profile.recon_scan, origin_lat, origin_lon, heading_rad, prefix="RECON_SCAN")
            if profile.recon_scan is not None and profile.recon_scan.waypoints
            else _build_recon_scan_points(recce_corners, scan_points)
        )
    except FieldReferenceError as exc:
        raise RuntimeFieldGeometryError(
            f"runtime FIELD→GPS projection failed: {exc}"
        ) from exc

    geometry = RuntimeFieldGeometry(
        profile_id=profile.profile_id,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        forward_marker_lat=marker_lat,
        forward_marker_lon=marker_lon,
        field_heading_yaw_rad=heading_rad,
        field_heading_deg=heading_deg,
        baseline_m=0.0,  # No physical baseline in RTK heading mode
        home=home,
        forward_marker=fwd,
        drop_scan_waypoints=tuple(scan_points),
        recon_scan_waypoints=tuple(recon_scan_points),
        drop_area_corners=tuple(drop_corners),
        recce_area_corners=tuple(recce_corners),
        warnings=tuple(warnings),
    )
    for point in (
        geometry.home,
        geometry.forward_marker,
        *geometry.drop_scan_waypoints,
        *geometry.recon_scan_waypoints,
        *geometry.drop_area_corners,
        *geometry.recce_area_corners,
    ):
        _validate_generated_point(point)
    return geometry


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _validate_inputs(
    profile: FieldProfile,
    origin_lat: float,
    origin_lon: float,
) -> None:
    if not isinstance(profile, FieldProfile):
        raise RuntimeFieldGeometryError("profile must be a FieldProfile instance")
    if profile.schema_version != 3:
        raise RuntimeFieldGeometryError(
            f"only schema v3 supported, got v{profile.schema_version}"
        )

    diag = validate_field_profile(profile)
    if not diag.ok:
        raise RuntimeFieldGeometryError(
            f"profile validation failed: {'; '.join(diag.errors)}"
        )

    if profile.forward_marker is None:
        raise RuntimeFieldGeometryError("profile has no forward_marker")
    if profile.drop_scan is None:
        raise RuntimeFieldGeometryError("profile has no drop_scan")
    if profile.drop_scan.waypoints is None or len(profile.drop_scan.waypoints) != 4:
        raise RuntimeFieldGeometryError("profile.drop_scan must have exactly 4 waypoints")

    if (
        profile.field_geometry.drop_area_y_min is None
        or profile.field_geometry.drop_area_y_max is None
        or profile.field_geometry.recce_area_y_min is None
        or profile.field_geometry.recce_area_y_max is None
    ):
        raise RuntimeFieldGeometryError("profile.field_geometry area boundaries must not be None")

    try:
        validate_wgs84_lat_lon(origin_lat, origin_lon, reject_pole=True)
        marker = profile.forward_marker
        if marker is not None:
            validate_wgs84_lat_lon(marker.lat, marker.lon, reject_pole=True)
    except FieldReferenceError as exc:
        raise RuntimeFieldGeometryError(
            f"invalid origin_lat/origin_lon or forward marker: {exc}"
        ) from exc


def _validate_generated_point(point: RuntimeFieldPoint) -> None:
    for name in ("field_x_m", "field_y_m", "altitude_m"):
        value = getattr(point, name)
        if not (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            raise RuntimeFieldGeometryError(
                f"generated point {point.name}.{name} is not finite"
            )
    try:
        _, lon = validate_wgs84_lat_lon(
            point.lat, point.lon, reject_pole=True
        )
    except FieldReferenceError as exc:
        raise RuntimeFieldGeometryError(
            f"generated point {point.name} has invalid WGS84: {exc}"
        ) from exc
    if lon != normalize_longitude_deg(lon):
        raise RuntimeFieldGeometryError(
            f"generated point {point.name} longitude is not canonical"
        )


def _normalize_yaw(yaw_rad: float) -> float:
    yaw = math.atan2(math.sin(yaw_rad), math.cos(yaw_rad))
    if yaw <= -math.pi + 1e-15:
        yaw = math.pi
    return yaw


def _build_scan_points(
    ds: DropScanConfig,
    origin_lat: float,
    origin_lon: float,
    heading_rad: float,
    prefix: str = "DROP_SCAN",
) -> list[RuntimeFieldPoint]:
    points: list[RuntimeFieldPoint] = []
    for i, wp in enumerate(ds.waypoints):
        gps = field_to_gps_from_origin(
            wp.x_m,
            wp.y_m,
            wp.altitude_m,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            field_heading_yaw_rad=heading_rad,
        )
        points.append(
            RuntimeFieldPoint(
                name=f"{prefix}_{i + 1}",
                field_x_m=wp.x_m,
                field_y_m=wp.y_m,
                altitude_m=wp.altitude_m,
                lat=gps.lat,
                lon=gps.lon,
            )
        )
    return points


def _build_area_corners(
    prefix: str,
    lane_half_width_m: float,
    y_min: float | None,
    y_max: float | None,
    origin_lat: float,
    origin_lon: float,
    heading_rad: float,
) -> list[RuntimeFieldPoint]:
    if y_min is None or y_max is None:
        return []
    lane = lane_half_width_m
    corners = [
        (f"{prefix}1", -lane, y_min),
        (f"{prefix}2",  lane, y_min),
        (f"{prefix}3",  lane, y_max),
        (f"{prefix}4", -lane, y_max),
    ]
    points: list[RuntimeFieldPoint] = []
    for name, fx, fy in corners:
        gps = field_to_gps_from_origin(
            fx, fy, 0.0,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            field_heading_yaw_rad=heading_rad,
        )
        points.append(
            RuntimeFieldPoint(
                name=name,
                field_x_m=fx,
                field_y_m=fy,
                altitude_m=0.0,
                lat=gps.lat,
                lon=gps.lon,
            )
        )
    return points


def _build_recon_scan_points(
    recce_corners: list[RuntimeFieldPoint], drop_scan_points: list[RuntimeFieldPoint],
) -> list[RuntimeFieldPoint]:
    """Turn the profile's existing recon rectangle into four GLOBAL scan points.

    The scan altitude deliberately follows the established drop scan profile;
    only the FIELD region and waypoint names differ.
    """
    return [
        RuntimeFieldPoint(
            name=f"RECON_SCAN_{index + 1}",
            field_x_m=corner.field_x_m,
            field_y_m=corner.field_y_m,
            altitude_m=drop_scan_points[index].altitude_m,
            lat=corner.lat,
            lon=corner.lon,
        )
        for index, corner in enumerate(recce_corners)
    ]
