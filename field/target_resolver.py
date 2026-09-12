"""Runtime field target resolver.

Resolves HOME and DROP_SCAN_1..4 from a frozen runtime field reference
status dict (JSON-safe, as produced by ``FieldService.status()``).

Consumes:
    context["field_reference"] = FieldService.status()["field_reference"]

Readiness requires ALL of:
    is_confirmed == True
    is_frozen == True
    is_ready_for_field_to_gps == True
    active_source == "runtime_origin_forward_marker"
    synced_to_runtime == True
    runtime_binding.state == "applied"
    runtime_binding.profile_id is non-empty
    runtime_binding.geometry is a valid dict with home + drop_scan_waypoints
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# target DTO
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GpsScanTarget:
    """A GLOBAL GPS waypoint target ready for flight dispatch.

    FIELD +Y is the fixed field-centre direction, so resolver-produced
    navigation targets deliberately face that heading.
    """

    name: str
    lat: float
    lon: float
    altitude_m: float
    yaw_mode: str = "field_heading"
    field_yaw_deg: float = 0.0


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------


class RuntimeFieldTargetResolver:
    """Resolve HOME + DROP_SCAN_1..4 from a JSON-safe field_reference status dict.

    Usage::

        resolver = RuntimeFieldTargetResolver(field_reference_dict)
        if resolver.is_ready:
            home = resolver.home(altitude_m=5.0)
            scans = resolver.scan_waypoints()
    """

    def __init__(self, field_reference: Dict[str, Any]) -> None:
        self._fr = field_reference or {}
        self._error: str | None = None
        self._home: Dict[str, Any] | None = None
        self._scan_waypoint_groups: dict[str, List[Dict[str, Any]]] = {}
        self._profile_id: str = ""

        # ── readiness checks ──────────────────────────────────────────
        if self._fr.get("is_confirmed") is not True:
            self._error = "field reference not confirmed"
            return
        if self._fr.get("is_frozen") is not True:
            self._error = "field reference not frozen"
            return
        if self._fr.get("is_ready_for_field_to_gps") is not True:
            self._error = "field reference not ready for field→GPS"
            return
        if self._fr.get("active_source") != "runtime_origin_forward_marker":
            self._error = (
                f"active_source must be runtime_origin_forward_marker, "
                f"got {self._fr.get('active_source')!r}"
            )
            return
        if self._fr.get("synced_to_runtime") is not True:
            self._error = "field reference not synced to runtime"
            return

        rb = self._fr.get("runtime_binding")
        if not isinstance(rb, dict):
            self._error = "runtime_binding missing"
            return
        if rb.get("state") != "applied":
            self._error = f"runtime_binding.state must be applied, got {rb.get('state')!r}"
            return

        self._profile_id = str(rb.get("profile_id") or "")
        if not self._profile_id:
            self._error = "runtime_binding.profile_id is empty"
            return

        geometry = rb.get("geometry")
        if not isinstance(geometry, dict):
            self._error = "runtime_binding.geometry missing or not a dict"
            return

        home = geometry.get("home")
        if not isinstance(home, dict) or "lat" not in home or "lon" not in home:
            self._error = "geometry.home missing or invalid"
            return
        # Validate HOME WGS84
        try:
            hlat = float(home["lat"]); hlon = float(home["lon"])
            import math
            if not math.isfinite(hlat) or not math.isfinite(hlon):
                raise ValueError
            if not (-90.0 <= hlat <= 90.0) or not (-180.0 <= hlon <= 180.0):
                raise ValueError
        except (ValueError, TypeError):
            self._error = "geometry.home lat/lon not valid finite WGS84"
            return
        self._home = dict(home)

        for group, field, prefix in (("drop", "drop_scan_waypoints", "DROP_SCAN"), ("recon", "recon_scan_waypoints", "RECON_SCAN")):
            scans = geometry.get(field)
            if group == "recon" and scans is None:
                # Old frozen runtime contexts remain valid for the default drop
                # flow; a recon caller is rejected explicitly by scan_waypoints.
                continue
            if not isinstance(scans, list) or len(scans) != 4:
                self._error = f"geometry.{field} must be a list of 4, got {type(scans).__name__}"
                return
            for i, wp in enumerate(scans):
                if not isinstance(wp, dict) or "lat" not in wp or "lon" not in wp:
                    self._error = f"geometry.{field}[{i}] invalid"
                    return
                wname = str(wp.get("name", ""))
                expected = f"{prefix}_{i+1}"
                if wname != expected:
                    self._error = f"geometry.{field}[{i}] name={wname!r} expected={expected!r}"
                    return
                try:
                    wlat = float(wp["lat"]); wlon = float(wp["lon"]); walt = float(wp.get("altitude_m", 0))
                    import math
                    if not math.isfinite(wlat) or not math.isfinite(wlon) or not math.isfinite(walt): raise ValueError
                    if not (-90.0 <= wlat <= 90.0) or not (-180.0 <= wlon <= 180.0): raise ValueError
                    if walt <= 0.0: raise ValueError("altitude_m must be > 0")
                except (ValueError, TypeError) as e:
                    self._error = f"geometry.{field}[{i}] invalid: {e}"
                    return
            self._scan_waypoint_groups[group] = [dict(wp) for wp in scans]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._error is None

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def profile_id(self) -> str:
        return self._profile_id

    @staticmethod
    def _parse_positive_altitude(value: object, name: str) -> float:
        """Validate and return a positive finite altitude in metres."""
        if value is None:
            raise RuntimeFieldTargetError(f"{name} must be provided (got None)")
        if isinstance(value, bool):
            raise RuntimeFieldTargetError(f"{name} must be a number, not bool")
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise RuntimeFieldTargetError(f"{name} must be a finite number, got {value!r}")
        import math
        if not math.isfinite(f):
            raise RuntimeFieldTargetError(f"{name} must be finite, got {value!r}")
        if f <= 0.0:
            raise RuntimeFieldTargetError(f"{name} must be > 0, got {f}")
        return f

    def home(self, altitude_m: float | None = None) -> GpsScanTarget:
        """Return HOME as a GLOBAL GPS target.

        ``altitude_m`` MUST be provided (> 0) — HOME GPS point has
        altitude 0 and cannot be used directly for flight.
        """
        self._require_ready()
        h = self._home
        alt = self._parse_positive_altitude(altitude_m, "HOME altitude_m")
        return GpsScanTarget(
            name="HOME",
            lat=float(h["lat"]),
            lon=float(h["lon"]),
            altitude_m=float(alt),
            yaw_mode="field_heading",
        )

    def scan_waypoints(
        self, altitude_overrides: Dict[str, float] | None = None, group: str = "drop"
    ) -> Tuple[GpsScanTarget, ...]:
        """Return DROP_SCAN_1..4 as GLOBAL GPS targets.

        Optionally override altitude_m per waypoint name.
        """
        self._require_ready()
        if group not in self._scan_waypoint_groups:
            raise RuntimeFieldTargetError(f"unknown scan waypoint group: {group!r}")
        overrides = altitude_overrides or {}
        result: list[GpsScanTarget] = []
        for wp in self._scan_waypoint_groups[group]:
            name = str(wp.get("name", ""))
            if name in overrides:
                alt = self._parse_positive_altitude(overrides[name], f"{name} altitude_m override")
            else:
                alt = float(wp.get("altitude_m", 0.0))
                if alt <= 0.0:
                    raise RuntimeFieldTargetError(f"{name} altitude_m must be > 0, got {alt}")
            result.append(GpsScanTarget(
                name=name,
                lat=float(wp["lat"]),
                lon=float(wp["lon"]),
                altitude_m=float(alt),
                yaw_mode="field_heading",
            ))
        return tuple(result)

    def target_by_name(self, name: str, altitude_m: float | None = None) -> GpsScanTarget:
        """Resolve a named target."""
        self._require_ready()
        if name == "HOME":
            return self.home(altitude_m=altitude_m)
        for wp in self._scan_waypoint_groups["drop"]:
            if wp.get("name") == name:
                if altitude_m is not None:
                    alt = self._parse_positive_altitude(altitude_m, f"{name} altitude_m override")
                else:
                    alt = float(wp.get("altitude_m", 0.0))
                    if alt <= 0.0:
                        raise RuntimeFieldTargetError(f"{name} altitude_m must be > 0, got {alt}")
                return GpsScanTarget(
                    name=name,
                    lat=float(wp["lat"]),
                    lon=float(wp["lon"]),
                    altitude_m=float(alt),
                    yaw_mode="field_heading",
                )
        raise RuntimeFieldTargetError(f"unknown target: {name!r}")

    def as_action_dict(
        self, name: str, altitude_m: float | None = None
    ) -> Dict[str, Any]:
        """Return a dict suitable for ``GotoWaypointAction.start()``
        using lat/lon GLOBAL input.

        Produces::

            {"lat": <lat>, "lon": <lon>, "altitude_m": <alt>,
             "target_frame": "global", "waypoint_mode": "absolute",
             "yaw_mode": "field_heading", "field_yaw_deg": 0.0}
        """
        t = self.target_by_name(name, altitude_m=altitude_m)
        return {
            "lat": t.lat,
            "lon": t.lon,
            "altitude_m": t.altitude_m,
            "target_frame": "global",
            "waypoint_mode": "absolute",
            "yaw_mode": t.yaw_mode,
            "field_yaw_deg": t.field_yaw_deg,
            "key": f"global_scan_{name}",
        }

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _require_ready(self) -> None:
        if not self.is_ready:
            raise RuntimeFieldTargetError(
                self._error or "runtime field target resolver not ready"
            )


class RuntimeFieldTargetError(RuntimeError):
    """Runtime field target resolution failed."""
