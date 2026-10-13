"""Atomic fusion of raw estimates captured by Mission steps."""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from field.coordinates import field_to_gps_from_origin, gps_to_field_from_origin
from field.models import gps_enu_deltas
from guidance.gps_derived_enu_fusion import GpsDerivedEnuFusion, GpsFusionConfig
from guidance.target_projection import GpsRawEstimate

from .base import ActionModule
from .result import ActionResult


class GpsFuseViewsAction(ActionModule):
    def __init__(self) -> None:
        self.reset()

    def start(self, params: dict[str, Any] | None = None) -> None:
        data = params or {}
        raw_views = data.get("views", [])
        if not isinstance(raw_views, list):
            raise ValueError("views must be a list")
        self.raw_estimates: list[GpsRawEstimate] = []
        for view in raw_views:
            values = view.get("raw_estimates", []) if isinstance(view, dict) else []
            for value in values if isinstance(values, list) else []:
                if isinstance(value, dict):
                    self.raw_estimates.append(GpsRawEstimate(**{
                        name: value.get(name)
                        for name in GpsRawEstimate.__dataclass_fields__
                    }))
        fusion = dict(data.get("fusion") or {})
        self.config = GpsFusionConfig(**{
            name: value for name, value in fusion.items()
            if name in GpsFusionConfig.__dataclass_fields__
        })
        self.class_names = {str(value) for value in data.get("class_names", [])} or None
        raw_limit = data.get("field_x_limit_m")
        self.field_x_limit_m = None if raw_limit is None else float(raw_limit)
        if self.field_x_limit_m is not None and (not math.isfinite(self.field_x_limit_m) or self.field_x_limit_m <= 0):
            raise ValueError("field_x_limit_m must be positive and finite")
        self.started, self.stopped, self.done = True, False, False
        self.last_detail: dict[str, Any] = {}

    def update(self, context: dict[str, Any] | None = None) -> ActionResult:
        if not self.started:
            return ActionResult(failed=True, reason="action_not_started")
        if self.stopped:
            return ActionResult(done=True, reason="stopped", output=dict(self.last_detail), detail=self.last_detail)
        if self.done:
            return ActionResult(done=True, reason="gps_views_fused", output=dict(self.last_detail), detail=self.last_detail)
        reference = (context or {}).get("field_reference")
        if not isinstance(reference, dict) or not reference.get("is_ready_for_field_to_gps"):
            return ActionResult(failed=True, reason="field_reference_not_ready")
        try:
            origin_lat = float(reference["origin_lat"])
            origin_lon = float(reference["origin_lon"])
        except (KeyError, TypeError, ValueError):
            return ActionResult(failed=True, reason="field_reference_origin_missing")
        objects = GpsDerivedEnuFusion(
            origin_lat=origin_lat, origin_lon=origin_lon,
            config=self.config, class_names=self.class_names,
        ).fuse(self.raw_estimates)
        localized = [_output_item(item) for item in objects]
        if self.field_x_limit_m is not None:
            heading = (context or {}).get("field_heading_yaw_rad")
            if not isinstance(heading, (int, float)) or not math.isfinite(float(heading)):
                return ActionResult(failed=True, reason="field_heading_not_ready")
            for item in localized:
                point = gps_to_field_from_origin(
                    item["lat"], item["lon"], 0.0,
                    origin_lat=origin_lat, origin_lon=origin_lon,
                    field_heading_yaw_rad=float(heading),
                )
                field_x = max(-self.field_x_limit_m, min(self.field_x_limit_m, point.field_x_m))
                if field_x == point.field_x_m:
                    continue
                gps = field_to_gps_from_origin(
                    field_x, point.field_y_m, 0.0,
                    origin_lat=origin_lat, origin_lon=origin_lon,
                    field_heading_yaw_rad=float(heading),
                )
                item["lat"], item["lon"] = gps.lat, gps.lon
                item["north_m"], item["east_m"] = gps_enu_deltas(
                    origin_lat, origin_lon, gps.lat, gps.lon,
                )
        self.last_detail = {"localized_objects": localized, "objects": localized,
                            "raw_estimates_count": len(self.raw_estimates),
                            "count": len(localized), "coordinate_frame": "GLOBAL"}
        self.done = True
        reason = "gps_views_fused" if localized else "gps_views_fused_empty"
        return ActionResult(done=True, reason=reason, output=dict(self.last_detail), detail=self.last_detail)

    def stop(self) -> None: self.stopped = True

    def reset(self) -> None:
        self.raw_estimates = []
        self.field_x_limit_m = None
        self.started = self.stopped = self.done = False
        self.last_detail = {}


def _output_item(item: Any) -> dict[str, Any]:
    """Serialize fused DTOs to the JSON-array shapes promised by Contract v1."""
    output = asdict(item)
    # ``sample_count`` is the number of post-outlier observations actually
    # supporting the fused target.  Publish the generic aliases consumed by
    # target selection as well, so it never mistakes pre-outlier ``raw_count``
    # for effective evidence.
    output["seen_count"] = int(item.sample_count)
    output["count"] = int(item.sample_count)
    output["source_waypoints"] = list(item.source_waypoints)
    output["source_frames"] = list(item.source_frames)
    return output
