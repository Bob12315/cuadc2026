"""Runtime field binding — pure GPS sampling and binding candidate.

Deterministic sampling layer: Schema v3 profile + DroneState snapshots →
GPS quality filtering → duplicate detection → 5 s window → ≥20 samples →
median origin → horizontal spread → RuntimeFieldBindingCandidate.

No hardware access, no global state, no RuntimeContext writes.
All time values are supplied by the caller.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from .profile import (
    FieldProfile,
    GpsQualityThresholds,
    RuntimeOriginSampling,
    validate_field_profile,
)
from .models import (
    FieldReference,
    FieldReferenceError,
    HeadingSource,
    OriginSource,
    WGS84_POLE_COS_EPS,
    circular_median_longitude_deg,
    gps_enu_deltas,
    normalize_longitude_deg,
    shortest_longitude_delta_deg,
    validate_wgs84_lat_lon,
)
from .coordinates import field_to_gps_from_origin
from .geometry import (
    RuntimeFieldGeometry,
    RuntimeFieldGeometryError,
    RuntimeFieldPoint,
    build_runtime_field_geometry,
    build_runtime_field_geometry_from_heading,
)


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------


class RuntimeFieldBindingError(ValueError):
    """Invalid sampling state, GPS observation, or binding candidate."""


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeGpsSample:
    """A single accepted GPS sample."""

    source_time_s: float
    observed_at_s: float
    lat: float
    lon: float
    fix_type: int
    satellites: int
    eph: float
    epv: float


@dataclass(frozen=True, slots=True)
class RuntimeFieldSamplingStatus:
    """Snapshot of the sampler's current state."""

    state: str
    profile_id: str

    started_at_s: Optional[float]
    elapsed_s: float
    sample_window_s: float
    min_samples: int

    accepted_samples: int
    rejected_samples: int
    duplicate_samples: int

    window_complete: bool
    can_finalize: bool

    last_source_time_s: Optional[float]
    last_rejection_reason: Optional[str]


@dataclass(frozen=True, slots=True)
class RuntimeFieldBindingCandidate:
    """An immutable, not-yet-applied field binding candidate.

    Has not been written into FieldReference — not confirmed, not frozen.
    """

    profile_id: str

    origin_source: str
    heading_source: str
    field_reference_mode: str

    origin_lat: float
    origin_lon: float

    forward_marker_lat: float
    forward_marker_lon: float

    field_heading_yaw_rad: float
    field_heading_deg: float
    baseline_m: float

    sample_count: int
    rejected_sample_count: int
    duplicate_sample_count: int

    started_at_s: float
    completed_at_s: float
    sample_duration_s: float
    horizontal_spread_m: float

    gps_fix_type: int
    gps_satellites: int
    gps_eph: float
    gps_epv: float

    geometry: RuntimeFieldGeometry
    warnings: Tuple[str, ...]


# ---------------------------------------------------------------------------
# sampler
# ---------------------------------------------------------------------------


class RuntimeFieldBindingSampler:
    """Deterministic GPS sampling state machine.

    Builds a binding candidate from a Schema v3 profile and a sequence
    of drone-state snapshots.  All time values are caller-supplied.

    heading_mode:
        - "forward_marker" (default): heading from profile forward marker A→B
        - "rtk": heading from median of sampled yaw_rad (RTK dual-antenna)
    """

    def __init__(self, profile: FieldProfile, *, heading_mode: str = "forward_marker") -> None:
        if not isinstance(profile, FieldProfile):
            raise RuntimeFieldBindingError("profile must be a FieldProfile instance")
        if profile.schema_version != 3:
            raise RuntimeFieldBindingError(
                f"only schema v3 supported, got v{profile.schema_version}"
            )
        if heading_mode not in ("forward_marker", "rtk"):
            raise RuntimeFieldBindingError(
                f"heading_mode must be 'forward_marker' or 'rtk', got {heading_mode!r}"
            )
        diag = validate_field_profile(profile)
        if not diag.ok:
            raise RuntimeFieldBindingError(
                f"profile validation failed: {'; '.join(diag.errors)}"
            )

        self._profile: FieldProfile = profile
        self._gq: GpsQualityThresholds = profile.gps_quality
        self._ros: RuntimeOriginSampling = profile.runtime_origin_sampling
        self._heading_mode: str = heading_mode

        self._state: str = "idle"
        self._started_at_s: Optional[float] = None
        self._completed_at_s: Optional[float] = None

        self._accepted: list[RuntimeGpsSample] = []
        self._rejected: int = 0
        self._duplicate: int = 0
        self._seen_source_times: set[float] = set()
        self._max_seen_source_time_s: Optional[float] = None
        self._last_rejection_reason: Optional[str] = None

        # RTK heading mode: collect yaw samples alongside GPS
        self._yaw_samples: list[float] = []

        self._candidate: Optional[RuntimeFieldBindingCandidate] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self, *, started_at_s: float) -> RuntimeFieldSamplingStatus:
        """Begin a new sampling session."""
        if not _is_finite_number(started_at_s):
            raise RuntimeFieldBindingError(
                f"started_at_s must be a finite number, got {started_at_s!r}"
            )
        if self._state == "sampling":
            raise RuntimeFieldBindingError(
                "cannot start a new session while sampling is in progress; call reset() first"
            )

        self._state = "sampling"
        self._started_at_s = float(started_at_s)
        self._completed_at_s = None
        self._accepted = []
        self._rejected = 0
        self._duplicate = 0
        self._seen_source_times = set()
        self._max_seen_source_time_s = None
        self._last_rejection_reason = None
        self._yaw_samples = []
        self._candidate = None

        return self.status(now_s=started_at_s)

    def reset(self) -> RuntimeFieldSamplingStatus:
        """Reset to idle regardless of current state."""
        self._state = "idle"
        self._started_at_s = None
        self._completed_at_s = None
        self._accepted = []
        self._rejected = 0
        self._duplicate = 0
        self._seen_source_times = set()
        self._max_seen_source_time_s = None
        self._last_rejection_reason = None
        self._yaw_samples = []
        self._candidate = None
        return self.status()

    def status(
        self, *, now_s: Optional[float] = None
    ) -> RuntimeFieldSamplingStatus:
        """Return the current sampling status."""
        profile_id = self._profile.profile_id
        window_s = self._ros.sample_window_s
        min_samples = self._ros.min_samples

        if self._state == "idle":
            return RuntimeFieldSamplingStatus(
                state="idle", profile_id=profile_id,
                started_at_s=None, elapsed_s=0.0,
                sample_window_s=window_s, min_samples=min_samples,
                accepted_samples=0, rejected_samples=0, duplicate_samples=0,
                window_complete=False, can_finalize=False,
                last_source_time_s=None, last_rejection_reason=None,
            )

        if self._state == "sampling":
            if now_s is None:
                raise RuntimeFieldBindingError("now_s is required when sampling")
            if not _is_finite_number(now_s):
                raise RuntimeFieldBindingError(f"now_s must be finite, got {now_s!r}")
            started = self._started_at_s
            assert started is not None
            if now_s < started:
                raise RuntimeFieldBindingError(
                    f"now_s ({now_s}) < started_at_s ({started})"
                )
            elapsed = now_s - started
            window_complete = elapsed >= window_s
            can_finalize = (
                window_complete and self._accepted_n >= min_samples
            )
            return RuntimeFieldSamplingStatus(
                state="sampling", profile_id=profile_id,
                started_at_s=started, elapsed_s=elapsed,
                sample_window_s=window_s, min_samples=min_samples,
                accepted_samples=self._accepted_n,
                rejected_samples=self._rejected,
                duplicate_samples=self._duplicate,
                window_complete=window_complete,
                can_finalize=can_finalize,
                last_source_time_s=self._max_seen_source_time_s,
                last_rejection_reason=self._last_rejection_reason,
            )

        # ready or failed
        started = self._started_at_s
        completed = self._completed_at_s
        if started is None or completed is None:
            elapsed = 0.0
        elif now_s is None:
            elapsed = completed - started
        else:
            if not _is_finite_number(now_s):
                raise RuntimeFieldBindingError(
                    f"now_s must be a finite number, got {now_s!r}"
                )
            if now_s < started:
                raise RuntimeFieldBindingError(
                    f"now_s ({now_s}) < started_at_s ({started})"
                )
            effective = max(completed, float(now_s))
            elapsed = max(0.0, effective - started)
        window_complete = elapsed >= window_s

        return RuntimeFieldSamplingStatus(
            state=self._state, profile_id=profile_id,
            started_at_s=started, elapsed_s=elapsed,
            sample_window_s=window_s, min_samples=min_samples,
            accepted_samples=self._accepted_n,
            rejected_samples=self._rejected,
            duplicate_samples=self._duplicate,
            window_complete=window_complete, can_finalize=False,
            last_source_time_s=self._max_seen_source_time_s,
            last_rejection_reason=self._last_rejection_reason,
        )

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def observe_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        observed_at_s: float,
    ) -> RuntimeFieldSamplingStatus:
        """Ingest one drone-state snapshot during sampling."""

        if self._state != "sampling":
            raise RuntimeFieldBindingError(
                f"cannot observe in state {self._state!r}"
            )

        if not _is_finite_number(observed_at_s):
            raise RuntimeFieldBindingError(
                f"observed_at_s must be a finite number, got {observed_at_s!r}"
            )
        started = self._started_at_s
        assert started is not None
        if observed_at_s < started:
            raise RuntimeFieldBindingError(
                f"observed_at_s ({observed_at_s}) < started_at_s ({started})"
            )

        # Window check — reject snapshots that arrive after the window closes
        window_s = self._ros.sample_window_s
        if observed_at_s > started + window_s:
            return self.status(now_s=observed_at_s)

        # Defend against non-Mapping snapshot
        if not isinstance(snapshot, Mapping):
            self._rejected += 1
            self._last_rejection_reason = (
                "snapshot must be a mapping"
            )
            return self.status(now_s=observed_at_s)

        # Extract source time
        src_time = snapshot.get("last_global_position_time")
        if src_time is None:
            self._rejected += 1
            self._last_rejection_reason = "missing last_global_position_time"
            return self.status(now_s=observed_at_s)
        if not _is_finite_number(src_time) or float(src_time) <= 0.0:
            self._rejected += 1
            self._last_rejection_reason = (
                f"last_global_position_time must be a finite positive number, "
                f"got {src_time!r}"
            )
            return self.status(now_s=observed_at_s)

        src_time_f = float(src_time)

        # Duplicate check
        if src_time_f in self._seen_source_times:
            self._duplicate += 1
            return self.status(now_s=observed_at_s)

        # Non-monotonic check
        if self._max_seen_source_time_s is not None and src_time_f < self._max_seen_source_time_s:
            self._rejected += 1
            self._last_rejection_reason = (
                "last_global_position_time is not monotonic"
            )
            self._seen_source_times.add(src_time_f)
            return self.status(now_s=observed_at_s)

        # Mark source time as seen and advance max_seen BEFORE payload/quality
        # checks so repeated reads of the same bad GPS message are counted as
        # duplicates instead of repeatedly increasing rejected_samples, and so
        # that a later message with an older timestamp is correctly rejected as
        # non-monotonic.
        self._seen_source_times.add(src_time_f)
        self._max_seen_source_time_s = src_time_f

        # global_position_valid
        if snapshot.get("global_position_valid") is not True:
            self._rejected += 1
            self._last_rejection_reason = "global_position_valid is not True"
            return self.status(now_s=observed_at_s)

        # Lat/lon validation
        lat = snapshot.get("lat")
        lon = snapshot.get("lon")
        if not _is_finite_number(lat):
            self._rejected += 1
            self._last_rejection_reason = f"lat must be a finite number, got {lat!r}"
            return self.status(now_s=observed_at_s)
        if not _is_finite_number(lon):
            self._rejected += 1
            self._last_rejection_reason = f"lon must be a finite number, got {lon!r}"
            return self.status(now_s=observed_at_s)
        lat_f = float(lat)
        lon_f = float(lon)
        if lat_f < -90.0 or lat_f > 90.0:
            self._rejected += 1
            self._last_rejection_reason = f"lat {lat_f} out of range [-90, 90]"
            return self.status(now_s=observed_at_s)
        if lon_f < -180.0 or lon_f > 180.0:
            self._rejected += 1
            self._last_rejection_reason = f"lon {lon_f} out of range [-180, 180]"
            return self.status(now_s=observed_at_s)
        if abs(math.cos(math.radians(lat_f))) <= WGS84_POLE_COS_EPS:
            self._rejected += 1
            self._last_rejection_reason = "latitude too close to pole"
            return self.status(now_s=observed_at_s)

        # GPS quality checks
        gps_ok = True
        rejections: list[str] = []

        # fix_type — strict int
        fix = snapshot.get("gps_fix_type")
        if not _is_strict_int(fix):
            gps_ok = False
            rejections.append(f"gps_fix_type must be a strict integer, got {fix!r}")
        else:
            fix_i = int(fix)
            if fix_i < self._gq.min_fix_type:
                gps_ok = False
                rejections.append(
                    f"gps_fix_type {fix_i} < min_fix_type {self._gq.min_fix_type}"
                )

        # satellites — strict int
        sats = snapshot.get("satellites_visible")
        if not _is_strict_int(sats):
            gps_ok = False
            rejections.append(
                f"satellites_visible must be a strict integer, got {sats!r}"
            )
        else:
            sats_i = int(sats)
            if sats_i < self._gq.min_satellites:
                gps_ok = False
                rejections.append(
                    f"satellites_visible {sats_i} < min_satellites {self._gq.min_satellites}"
                )

        # eph — finite number, >= 0, <= max_eph
        eph = snapshot.get("gps_eph")
        if not _is_finite_number(eph):
            gps_ok = False
            rejections.append(f"gps_eph must be a finite number, got {eph!r}")
        else:
            eph_f = float(eph)
            if eph_f < 0.0:
                gps_ok = False
                rejections.append(f"gps_eph {eph_f} < 0")
            elif eph_f > self._gq.max_eph:
                gps_ok = False
                rejections.append(
                    f"gps_eph {eph_f} > max_eph {self._gq.max_eph}"
                )

        # epv — finite number, >= 0, <= max_epv
        epv = snapshot.get("gps_epv")
        if not _is_finite_number(epv):
            gps_ok = False
            rejections.append(f"gps_epv must be a finite number, got {epv!r}")
        else:
            epv_f = float(epv)
            if epv_f < 0.0:
                gps_ok = False
                rejections.append(f"gps_epv {epv_f} < 0")
            elif epv_f > self._gq.max_epv:
                gps_ok = False
                rejections.append(
                    f"gps_epv {epv_f} > max_epv {self._gq.max_epv}"
                )

        if not gps_ok:
            self._rejected += 1
            self._last_rejection_reason = "; ".join(rejections)
            return self.status(now_s=observed_at_s)

        # Accept
        sample = RuntimeGpsSample(
            source_time_s=src_time_f,
            observed_at_s=observed_at_s,
            lat=lat_f,
            lon=lon_f,
            fix_type=int(fix),
            satellites=int(sats),
            eph=float(eph),
            epv=float(epv),
        )
        self._accepted.append(sample)

        # RTK heading mode: also collect yaw from attitude
        if self._heading_mode == "rtk":
            yaw = snapshot.get("yaw")
            attitude_valid = snapshot.get("attitude_valid", False)
            if (
                attitude_valid
                and _is_finite_number(yaw)
            ):
                self._yaw_samples.append(float(yaw))

        # Do NOT clear last_rejection_reason
        return self.status(now_s=observed_at_s)

    # ------------------------------------------------------------------
    # finalize / preview
    # ------------------------------------------------------------------

    def _build_candidate(
        self,
        *,
        completed_at_s: float,
    ) -> RuntimeFieldBindingCandidate:
        """Construct a binding candidate from the current accepted samples.

        Does NOT modify sampler state.  Pure computation — no side effects.
        Raises RuntimeFieldBindingError on any failure.
        """
        if self._state != "sampling":
            raise RuntimeFieldBindingError(
                f"cannot build candidate in state {self._state!r}"
            )

        if not _is_finite_number(completed_at_s):
            raise RuntimeFieldBindingError(
                f"completed_at_s must be a finite number, got {completed_at_s!r}"
            )
        started = self._started_at_s
        assert started is not None
        if completed_at_s < started:
            raise RuntimeFieldBindingError(
                f"completed_at_s ({completed_at_s}) < started_at_s ({started})"
            )

        elapsed = completed_at_s - started
        if elapsed < self._ros.sample_window_s:
            raise RuntimeFieldBindingError(
                f"sampling window not yet complete: elapsed {elapsed:.2f}s "
                f"< {self._ros.sample_window_s}s"
            )

        n = self._accepted_n
        if n < self._ros.min_samples:
            raise RuntimeFieldBindingError(
                f"accepted samples {n} < required {self._ros.min_samples}"
            )

        # Median origin
        origin_lat = statistics.median(s.lat for s in self._accepted)
        origin_lon = circular_median_longitude_deg(
            s.lon for s in self._accepted
        )

        # Horizontal spread
        radii: list[float] = []
        for s in self._accepted:
            dn, de = gps_enu_deltas(origin_lat, origin_lon, s.lat, s.lon)
            radii.append(math.hypot(dn, de))
        spread = max(radii) if radii else 0.0

        if spread > self._ros.max_horizontal_spread_m:
            raise RuntimeFieldBindingError(
                f"horizontal spread {spread:.3f}m exceeds "
                f"max_horizontal_spread_m {self._ros.max_horizontal_spread_m}m"
            )

        # Build runtime geometry
        if self._heading_mode == "rtk":
            # RTK dual-antenna: use circular median of collected yaw samples
            if not self._yaw_samples:
                raise RuntimeFieldBindingError(
                    "RTK heading mode requires at least one valid yaw sample"
                )
            heading_rad = _circular_median_yaw(self._yaw_samples)
            try:
                geometry = build_runtime_field_geometry_from_heading(
                    self._profile,
                    origin_lat=origin_lat,
                    origin_lon=origin_lon,
                    heading_rad=heading_rad,
                )
            except RuntimeFieldGeometryError as exc:
                raise RuntimeFieldBindingError(str(exc)) from exc
            heading_source = HeadingSource.RUNTIME_RTK_HEADING.value
            field_reference_mode = "runtime_origin_rtk_heading"
        else:
            try:
                geometry = build_runtime_field_geometry(
                    self._profile,
                    origin_lat=origin_lat,
                    origin_lon=origin_lon,
                )
            except RuntimeFieldGeometryError as exc:
                raise RuntimeFieldBindingError(str(exc)) from exc
            heading_source = HeadingSource.RUNTIME_FORWARD_MARKER.value
            field_reference_mode = "runtime_origin_forward_marker"

        # Conservative GPS quality
        gps_fix_type = min(s.fix_type for s in self._accepted)
        gps_satellites = min(s.satellites for s in self._accepted)
        gps_eph = max(s.eph for s in self._accepted)
        gps_epv = max(s.epv for s in self._accepted)

        return RuntimeFieldBindingCandidate(
            profile_id=self._profile.profile_id,
            origin_source=OriginSource.RUNTIME_CURRENT_GPS.value,
            heading_source=heading_source,
            field_reference_mode=field_reference_mode,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            forward_marker_lat=geometry.forward_marker_lat,
            forward_marker_lon=geometry.forward_marker_lon,
            field_heading_yaw_rad=geometry.field_heading_yaw_rad,
            field_heading_deg=geometry.field_heading_deg,
            baseline_m=geometry.baseline_m,
            sample_count=n,
            rejected_sample_count=self._rejected,
            duplicate_sample_count=self._duplicate,
            started_at_s=started,
            completed_at_s=completed_at_s,
            sample_duration_s=elapsed,
            horizontal_spread_m=spread,
            gps_fix_type=gps_fix_type,
            gps_satellites=gps_satellites,
            gps_eph=gps_eph,
            gps_epv=gps_epv,
            geometry=geometry,
            warnings=geometry.warnings,
        )

    def preview_candidate(
        self,
        *,
        completed_at_s: float,
    ) -> RuntimeFieldBindingCandidate:
        """Return a preview binding candidate without modifying sampler state.

        Calls ``_build_candidate()`` — same math as ``finalize()``.
        Does NOT set ``_state``, ``_completed_at_s``, or ``_candidate``.
        """
        return self._build_candidate(completed_at_s=completed_at_s)

    def finalize(
        self, *, completed_at_s: float
    ) -> RuntimeFieldBindingCandidate:
        """Produce a binding candidate from the completed sampling session."""

        if self._state == "ready":
            assert self._candidate is not None
            return self._candidate

        try:
            candidate = self._build_candidate(completed_at_s=completed_at_s)
        except RuntimeFieldBindingError:
            self._state = "failed"
            self._completed_at_s = completed_at_s
            raise

        self._state = "ready"
        self._completed_at_s = completed_at_s
        self._candidate = candidate
        return candidate

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @property
    def _accepted_n(self) -> int:
        return len(self._accepted)


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def validate_runtime_field_binding_candidate(
    candidate: object,
) -> tuple[str, ...]:
    """Validate a RuntimeFieldBindingCandidate.

    Shared by service and builder layers.
    Returns a tuple of error strings (empty = valid).
    Never raises ordinary exceptions.
    """
    if not isinstance(candidate, RuntimeFieldBindingCandidate):
        return (
            f"candidate must be RuntimeFieldBindingCandidate, got {type(candidate).__name__}",
        )
    try:
        return tuple(_validate_candidate(candidate))
    except Exception as exc:
        return (f"candidate validation failed safely: {type(exc).__name__}: {exc}",)


def _validate_candidate(c: RuntimeFieldBindingCandidate) -> list[str]:
    errs: list[str] = []
    for name in ("profile_id", "origin_source", "heading_source", "field_reference_mode"):
        value = getattr(c, name)
        if not isinstance(value, str) or not value.strip():
            errs.append(f"{name} must be a non-empty string, got {value!r}")
    if c.origin_source != OriginSource.RUNTIME_CURRENT_GPS.value:
        errs.append("origin_source must be 'runtime_current_gps'")
    # RTK heading mode carries no physical A→B baseline: the forward marker is
    # synthetic and heading comes from the median of sampled dual-antenna yaw.
    is_rtk = c.heading_source == HeadingSource.RUNTIME_RTK_HEADING.value
    if c.heading_source not in (
        HeadingSource.RUNTIME_FORWARD_MARKER.value,
        HeadingSource.RUNTIME_RTK_HEADING.value,
    ):
        errs.append(
            "heading_source must be 'runtime_forward_marker' or 'runtime_rtk_heading'"
        )
    expected_mode = (
        "runtime_origin_rtk_heading" if is_rtk
        else "runtime_origin_forward_marker"
    )
    if c.field_reference_mode != expected_mode:
        errs.append(f"field_reference_mode must be {expected_mode!r}")

    origin = _validate_wgs84_pair(errs, "candidate.origin", c.origin_lat, c.origin_lon)
    marker = _validate_wgs84_pair(
        errs, "candidate.forward_marker", c.forward_marker_lat, c.forward_marker_lon
    )
    recomputed: tuple[float, float, float] | None = None
    if origin is not None and marker is not None:
        try:
            dn, de = gps_enu_deltas(*origin, *marker)
            baseline = math.hypot(dn, de)
            heading = FieldReference._normalize_yaw(math.atan2(de, dn))
            recomputed = (baseline, heading, math.degrees(heading))
        except Exception as exc:
            errs.append(f"independent A→B recomputation failed: {exc}")

    for name in ("field_heading_yaw_rad", "field_heading_deg", "baseline_m"):
        value = getattr(c, name)
        if not _is_finite_number(value):
            errs.append(f"{name} must be finite, got {value!r}")
    if _is_finite_number(c.baseline_m):
        if is_rtk:
            if float(c.baseline_m) != 0.0:
                errs.append(
                    f"baseline_m must be 0.0 in RTK heading mode, got {c.baseline_m!r}"
                )
        elif float(c.baseline_m) <= 0.0:
            errs.append(f"baseline_m must be > 0, got {c.baseline_m!r}")
    if _is_finite_number(c.field_heading_yaw_rad):
        normalized = FieldReference._normalize_yaw(float(c.field_heading_yaw_rad))
        if not _close(c.field_heading_yaw_rad, normalized, 1e-12):
            errs.append("field_heading_yaw_rad must be normalized")
    # The synthetic-marker A→B recomputation is an independent check only for
    # forward_marker mode.  In RTK mode heading comes from sampled yaw, so the
    # geometry is cross-checked against the candidate heading in _validate_geometry.
    if recomputed is not None and not is_rtk:
        _check_close(errs, "candidate.baseline_m", c.baseline_m, recomputed[0])
        _check_close(errs, "candidate.field_heading_yaw_rad", c.field_heading_yaw_rad, recomputed[1])
        _check_close(errs, "candidate.field_heading_deg", c.field_heading_deg, recomputed[2])

    for name in ("sample_count", "rejected_sample_count", "duplicate_sample_count"):
        value = getattr(c, name)
        if not _is_strict_int(value) or value < 0:
            errs.append(f"{name} must be a strict integer >= 0, got {value!r}")
    if _is_strict_int(c.sample_count) and c.sample_count < 1:
        errs.append("sample_count must be >= 1")
    for name in ("gps_fix_type", "gps_satellites"):
        value = getattr(c, name)
        if not _is_strict_int(value) or value < 0:
            errs.append(f"{name} must be a strict integer >= 0, got {value!r}")
    for name in ("gps_eph", "gps_epv", "horizontal_spread_m"):
        value = getattr(c, name)
        if not _is_finite_number(value) or float(value) < 0.0:
            errs.append(f"{name} must be finite >= 0, got {value!r}")

    timing_ok = True
    for name in ("started_at_s", "completed_at_s", "sample_duration_s"):
        value = getattr(c, name)
        if not _is_finite_number(value):
            errs.append(f"{name} must be finite, got {value!r}")
            timing_ok = False
    if timing_ok:
        if c.completed_at_s < c.started_at_s:
            errs.append("completed_at_s must be >= started_at_s")
        _check_close(
            errs,
            "sample_duration_s",
            c.sample_duration_s,
            c.completed_at_s - c.started_at_s,
        )

    geometry = c.geometry
    if not isinstance(geometry, RuntimeFieldGeometry):
        errs.append(
            f"geometry must be RuntimeFieldGeometry, got {type(geometry).__name__}"
        )
    else:
        _validate_geometry(errs, c, geometry, origin, marker, recomputed)

    if not isinstance(c.warnings, tuple) or any(
        not isinstance(item, str) for item in c.warnings
    ):
        errs.append("candidate.warnings must be a tuple of strings")
    elif isinstance(geometry, RuntimeFieldGeometry) and c.warnings != geometry.warnings:
        errs.append("candidate.warnings != geometry.warnings")
    return errs


def _validate_geometry(
    errs: list[str],
    c: RuntimeFieldBindingCandidate,
    g: RuntimeFieldGeometry,
    origin: tuple[float, float] | None,
    marker: tuple[float, float] | None,
    recomputed: tuple[float, float, float] | None,
) -> None:
    is_rtk = c.heading_source == HeadingSource.RUNTIME_RTK_HEADING.value
    if not isinstance(g.profile_id, str) or not g.profile_id.strip():
        errs.append("geometry.profile_id must be a non-empty string")
    elif g.profile_id != c.profile_id:
        errs.append("geometry.profile_id != candidate.profile_id")
    g_origin = _validate_wgs84_pair(errs, "geometry.origin", g.origin_lat, g.origin_lon)
    g_marker = _validate_wgs84_pair(
        errs, "geometry.forward_marker scalar", g.forward_marker_lat, g.forward_marker_lon
    )
    if origin is not None and g_origin is not None:
        _check_gps(errs, "geometry.origin", g_origin, origin)
    if marker is not None and g_marker is not None:
        _check_gps(errs, "geometry.forward_marker scalar", g_marker, marker)
    for name in ("field_heading_yaw_rad", "field_heading_deg", "baseline_m"):
        if not _is_finite_number(getattr(g, name)):
            errs.append(f"geometry.{name} must be finite")
    if is_rtk:
        if _is_finite_number(g.baseline_m) and float(g.baseline_m) != 0.0:
            errs.append(
                f"geometry.baseline_m must be 0.0 in RTK heading mode, got {g.baseline_m!r}"
            )
    elif recomputed is not None:
        _check_close(errs, "geometry.baseline_m", g.baseline_m, recomputed[0])
        _check_close(errs, "geometry.field_heading_yaw_rad", g.field_heading_yaw_rad, recomputed[1])
        _check_close(errs, "geometry.field_heading_deg", g.field_heading_deg, recomputed[2])

    home_ok = _validate_runtime_field_point(g.home, path="geometry.home", errs=errs, expected_name="HOME")
    fwd_ok = _validate_runtime_field_point(g.forward_marker, path="geometry.forward_marker", errs=errs)
    if home_ok:
        _check_close(errs, "geometry.home.field_x_m", g.home.field_x_m, 0.0)
        _check_close(errs, "geometry.home.field_y_m", g.home.field_y_m, 0.0)
        _check_close(errs, "geometry.home.altitude_m", g.home.altitude_m, 0.0)
        if origin is not None:
            _check_gps(errs, "geometry.home", (g.home.lat, g.home.lon), origin)
    if fwd_ok:
        _check_close(errs, "geometry.forward_marker.field_x_m", g.forward_marker.field_x_m, 0.0)
        _check_close(errs, "geometry.forward_marker.altitude_m", g.forward_marker.altitude_m, 0.0)
        if recomputed is not None and not is_rtk:
            _check_close(errs, "geometry.forward_marker.field_y_m", g.forward_marker.field_y_m, recomputed[0])
        if marker is not None:
            _check_gps(errs, "geometry.forward_marker", (g.forward_marker.lat, g.forward_marker.lon), marker)

    scan_ok = _validate_point_collection(
        errs, "geometry.drop_scan_waypoints", g.drop_scan_waypoints,
        tuple(f"DROP_SCAN_{index}" for index in range(1, 5)),
    )
    recon_scan_ok = _validate_point_collection(
        errs, "geometry.recon_scan_waypoints", g.recon_scan_waypoints,
        tuple(f"RECON_SCAN_{index}" for index in range(1, 5)),
    )
    drop_ok = _validate_point_collection(
        errs, "geometry.drop_area_corners", g.drop_area_corners,
        ("D1", "D2", "D3", "D4"), zero_altitude=True,
    )
    recce_ok = _validate_point_collection(
        errs, "geometry.recce_area_corners", g.recce_area_corners,
        ("R1", "R2", "R3", "R4"), zero_altitude=True,
    )
    if drop_ok:
        _validate_rectangle(errs, "geometry.drop_area_corners", g.drop_area_corners)
    if recce_ok:
        _validate_rectangle(errs, "geometry.recce_area_corners", g.recce_area_corners)
    if drop_ok and recce_ok:
        for index in range(4):
            _check_close(
                errs,
                f"drop/recce lane x[{index}]",
                g.drop_area_corners[index].field_x_m,
                g.recce_area_corners[index].field_x_m,
            )

    # In RTK mode there is no independent A→B heading, so reproject using the
    # candidate's own (yaw-derived) heading to verify geometry consistency.
    reproj_heading = (
        c.field_heading_yaw_rad if is_rtk
        else (recomputed[1] if recomputed is not None else None)
    )
    if origin is not None and reproj_heading is not None:
        points: list[tuple[str, RuntimeFieldPoint]] = []
        if home_ok:
            points.append(("geometry.home", g.home))
        if fwd_ok:
            points.append(("geometry.forward_marker", g.forward_marker))
        if scan_ok:
            points.extend((f"geometry.drop_scan_waypoints[{i}]", p) for i, p in enumerate(g.drop_scan_waypoints))
        if recon_scan_ok:
            points.extend((f"geometry.recon_scan_waypoints[{i}]", p) for i, p in enumerate(g.recon_scan_waypoints))
        if drop_ok:
            points.extend((f"geometry.drop_area_corners[{i}]", p) for i, p in enumerate(g.drop_area_corners))
        if recce_ok:
            points.extend((f"geometry.recce_area_corners[{i}]", p) for i, p in enumerate(g.recce_area_corners))
        for path, point in points:
            try:
                projected = field_to_gps_from_origin(
                    point.field_x_m,
                    point.field_y_m,
                    point.altitude_m,
                    origin_lat=origin[0],
                    origin_lon=origin[1],
                    field_heading_yaw_rad=reproj_heading,
                )
                _check_gps(errs, f"{path} reprojection", (point.lat, point.lon), (projected.lat, projected.lon))
            except Exception as exc:
                errs.append(f"{path} reprojection failed: {exc}")

    if not isinstance(g.warnings, tuple) or any(
        not isinstance(item, str) for item in g.warnings
    ):
        errs.append("geometry.warnings must be a tuple of strings")


def _validate_runtime_field_point(
    point: object,
    *,
    path: str,
    errs: list[str],
    expected_name: str | None = None,
) -> bool:
    if not isinstance(point, RuntimeFieldPoint):
        errs.append(f"{path} must be RuntimeFieldPoint, got {type(point).__name__}")
        return False
    ok = True
    if not isinstance(point.name, str) or not point.name.strip():
        errs.append(f"{path}.name must be a non-empty string")
        ok = False
    elif expected_name is not None and point.name != expected_name:
        errs.append(f"{path}.name must be {expected_name!r}, got {point.name!r}")
        ok = False
    for name in ("field_x_m", "field_y_m", "altitude_m"):
        value = getattr(point, name)
        if not _is_finite_number(value):
            errs.append(f"{path}.{name} must be finite, got {value!r}")
            ok = False
    if _validate_wgs84_pair(errs, path, point.lat, point.lon) is None:
        ok = False
    return ok


def _validate_point_collection(
    errs: list[str],
    path: str,
    collection: object,
    names: tuple[str, ...],
    *,
    zero_altitude: bool = False,
) -> bool:
    if not isinstance(collection, tuple):
        errs.append(f"{path} must be a tuple, got {type(collection).__name__}")
        return False
    if len(collection) != len(names):
        errs.append(f"{path} must contain exactly {len(names)} points, got {len(collection)}")
        return False
    ok = True
    for index, (point, name) in enumerate(zip(collection, names)):
        point_ok = _validate_runtime_field_point(
            point, path=f"{path}[{index}]", errs=errs, expected_name=name
        )
        ok = point_ok and ok
        if point_ok and zero_altitude and not _close(point.altitude_m, 0.0):
            errs.append(f"{path}[{index}].altitude_m must be 0")
            ok = False
    return ok


def _validate_rectangle(
    errs: list[str], path: str, points: tuple[RuntimeFieldPoint, ...]
) -> None:
    p1, p2, p3, p4 = points
    for label, actual, expected in (
        ("1.x == 4.x", p1.field_x_m, p4.field_x_m),
        ("2.x == 3.x", p2.field_x_m, p3.field_x_m),
        ("1.y == 2.y", p1.field_y_m, p2.field_y_m),
        ("3.y == 4.y", p3.field_y_m, p4.field_y_m),
        ("left/right symmetry", p1.field_x_m, -p2.field_x_m),
    ):
        if not _close(actual, expected):
            errs.append(f"{path} violates rectangle rule {label}")
    if not p1.field_x_m < p2.field_x_m:
        errs.append(f"{path} requires left x < right x")
    if not p1.field_y_m < p3.field_y_m:
        errs.append(f"{path} requires near y < far y")


def _validate_wgs84_pair(
    errs: list[str], path: str, lat: object, lon: object
) -> tuple[float, float] | None:
    try:
        pair = validate_wgs84_lat_lon(lat, lon, reject_pole=True)
        if pair[1] != normalize_longitude_deg(pair[1]):
            errs.append(f"{path}.lon must be canonical [-180, 180)")
            return None
        return pair
    except FieldReferenceError as exc:
        errs.append(f"{path} invalid WGS84: {exc}")
        return None


def _check_gps(
    errs: list[str],
    path: str,
    actual: tuple[float, float],
    expected: tuple[float, float],
) -> None:
    if not _close(actual[0], expected[0], 1e-9):
        errs.append(f"{path}.lat mismatch")
    try:
        lon_delta = shortest_longitude_delta_deg(expected[1], actual[1])
    except FieldReferenceError:
        errs.append(f"{path}.lon invalid")
    else:
        if abs(lon_delta) > 1e-9:
            errs.append(f"{path}.lon mismatch")


def _check_close(
    errs: list[str], path: str, actual: object, expected: object
) -> None:
    if not _close(actual, expected):
        errs.append(f"{path} mismatch: {actual!r} != {expected!r}")


def _close(actual: object, expected: object, tolerance: float = 1e-9) -> bool:
    return (
        _is_finite_number(actual)
        and _is_finite_number(expected)
        and math.isclose(
            float(actual), float(expected), rel_tol=1e-9, abs_tol=tolerance
        )
    )


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _circular_median_yaw(yaw_samples: list[float]) -> float:
    """Compute circular median of yaw angles (radians, range (-pi, pi]).

    Handles wrap-around correctly by computing deltas relative to the
    first sample, taking the linear median of those deltas, then
    converting back.
    """
    if not yaw_samples:
        raise RuntimeFieldBindingError("yaw_samples must not be empty")
    reference = yaw_samples[0]
    deltas: list[float] = []
    for yaw in yaw_samples:
        delta = math.atan2(
            math.sin(yaw - reference), math.cos(yaw - reference)
        )
        deltas.append(delta)
    median_delta = statistics.median(deltas)
    result = reference + median_delta
    # Normalize to (-pi, pi]
    return math.atan2(math.sin(result), math.cos(result))
