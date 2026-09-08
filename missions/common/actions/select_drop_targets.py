from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from guidance.waypoint_math import field_xy_to_enu

from .base import ActionModule
from .result import ActionResult

DEFAULT_SCORE_TABLE = {
    "bucket_1": 500,
    "bucket_2": 300,
    "bucket_3": 100,
    "bucket": 50,
}
DEFAULT_CLASS_ORDER = ["bucket_1", "bucket_2", "bucket_3", "bucket"]


@dataclass(slots=True)
class _Candidate:
    original: dict[str, Any]
    index: int
    id: str
    target_id: str | None
    class_name: str
    local_x: float
    local_y: float
    score: float
    seen_count: int
    raw_count: int
    weight: float
    order_index: int
    zone_distance_m: float | None


class SelectDropTargetsAction(ActionModule):
    def __init__(self) -> None:
        self.reset()

    def start(self, params: dict[str, Any] | None = None) -> None:
        data = params or {}
        objects = data.get("objects", [])
        self.input_key = str(data.get("input_key", "")).strip() or None
        if not isinstance(objects, list):
            if self.input_key is not None:
                objects = []
            else:
                raise ValueError("objects must be a list")
        target_count = int(data.get("target_count", 2))
        min_seen_count = int(data.get("min_seen_count", 2))
        min_raw_count = int(data.get("min_raw_count", 0))
        min_weight = float(data.get("min_weight", 0.0))
        deduplicate_radius_m = float(data.get("deduplicate_radius_m", 0.35))
        if target_count < 1:
            raise ValueError("target_count must be at least 1")
        if min_seen_count < 0:
            raise ValueError("min_seen_count must be non-negative")
        if min_raw_count < 0:
            raise ValueError("min_raw_count must be non-negative")
        if min_weight < 0.0:
            raise ValueError("min_weight must be non-negative")
        if deduplicate_radius_m < 0.0:
            raise ValueError("deduplicate_radius_m must be non-negative")

        score_table = data.get("score_table", DEFAULT_SCORE_TABLE)
        if not isinstance(score_table, dict):
            raise ValueError("score_table must be a dict")
        prefer_class_order = data.get("prefer_class_order", DEFAULT_CLASS_ORDER)
        if not isinstance(prefer_class_order, (list, tuple)):
            raise ValueError("prefer_class_order must be a list or tuple")

        self.objects = list(objects)
        self.target_count = target_count
        self.allow_fewer = self._bool_param(data.get("allow_fewer", False), "allow_fewer")
        self.score_table = {str(key): float(value) for key, value in score_table.items()}
        self.min_seen_count = min_seen_count
        self.min_raw_count = min_raw_count
        self.min_weight = min_weight
        self.require_local_xy = self._bool_param(data.get("require_local_xy", True), "require_local_xy")
        self.deduplicate_radius_m = deduplicate_radius_m
        self.prefer_class_order = [str(value) for value in prefer_class_order]
        self.single_target_servo_outputs = self._servo_outputs(
            data.get("single_target_servo_outputs"), "single_target_servo_outputs"
        )
        self.multi_target_first_servo_outputs = self._servo_outputs(
            data.get("multi_target_first_servo_outputs"), "multi_target_first_servo_outputs"
        )
        self.fallback_target = self._fallback_target(data.get("fallback_target"))
        self.key = str(data.get("key") or "").strip() or "select_drop_targets"
        self.zone_center = self._zone_center(data.get("zone_center"))
        self.zone_center_mode = str(data.get("zone_center_mode", "local"))
        if self.zone_center_mode not in ("local", "field"):
            raise ValueError("zone_center_mode must be 'local' or 'field'")
        self.coordinate_mode = str(data.get("coordinate_mode", "local")).strip().lower()
        if self.coordinate_mode not in ("local", "gps_enu"):
            raise ValueError("coordinate_mode must be 'local' or 'gps_enu'")
        self.started = True
        self.stopped = False
        self.done = False
        self.failed = False
        self.last_result = None

    def update(self, context: dict[str, Any] | None = None) -> ActionResult:
        if not self.started:
            return ActionResult(failed=True, reason="action_not_started")
        if self.stopped:
            return ActionResult(done=True, reason="stopped", detail=self._base_detail([], []))
        if self.done or self.failed:
            return self._clone_result(self.last_result)

        self._effective_zone_center = self.zone_center
        if self.zone_center_mode == "field" and self.zone_center is not None:
            if self.coordinate_mode != "gps_enu":
                return ActionResult(failed=True, reason="field_zone_center_requires_gps_enu")
            heading = self._float_context(context or {}, "field_heading_yaw_rad")
            if not bool((context or {}).get("field_gps_transform_confirmed")) or heading is None:
                return ActionResult(failed=True, reason="missing_field_reference_for_zone_center")
            self._effective_zone_center = field_xy_to_enu(self.zone_center[0], self.zone_center[1], heading)
        # input_key fallback: read objects from context at runtime
        if self.input_key is not None and not self.objects:
            ctx = context or {}
            objs = ctx.get(self.input_key)
            if isinstance(objs, list):
                self.objects = list(objs)

        result = self._select()
        self.done = result.done
        self.failed = result.failed
        self.last_result = result
        return result

    def stop(self) -> None:
        self.stopped = True

    def reset(self) -> None:
        self.objects: list[Any] = []
        self.input_key: str | None = None
        self.target_count = 2
        self.allow_fewer = False
        self.score_table = dict(DEFAULT_SCORE_TABLE)
        self.min_seen_count = 2
        self.min_raw_count = 0
        self.min_weight = 0.0
        self.require_local_xy = True
        self.deduplicate_radius_m = 0.35
        self.prefer_class_order = list(DEFAULT_CLASS_ORDER)
        self.single_target_servo_outputs: list[dict[str, Any]] | None = None
        self.multi_target_first_servo_outputs: list[dict[str, Any]] | None = None
        self.fallback_target: dict[str, Any] | None = None
        self.zone_center: tuple[float, float] | None = None
        self.zone_center_mode = "local"
        self.coordinate_mode = "local"
        self._effective_zone_center: tuple[float, float] | None = None
        self.key = "select_drop_targets"
        self.started = False
        self.stopped = False
        self.done = False
        self.failed = False
        self.last_result: ActionResult | None = None

    def _select(self) -> ActionResult:
        if not self.objects:
            detail = self._base_detail([], [])
            if self.allow_fewer:
                return ActionResult(done=True, reason="drop_targets_selected_empty", output=self._output(detail), detail=detail)
            return ActionResult(failed=True, reason="no_drop_objects", detail=detail)

        candidates: list[_Candidate] = []
        rejected: list[dict[str, Any]] = []
        for index, item in enumerate(self.objects):
            if not isinstance(item, dict):
                rejected.append({"id": f"target_{index}", "class_name": "bucket", "reason": "invalid_object"})
                continue
            candidate, rejection = self._candidate(item, index)
            if rejection is not None:
                rejected.append(rejection)
            elif candidate is not None:
                candidates.append(candidate)

        if not candidates:
            detail = self._base_detail([], rejected)
            if self.allow_fewer:
                return ActionResult(done=True, reason="drop_targets_selected_empty", output=self._output(detail), detail=detail)
            return ActionResult(failed=True, reason="no_valid_drop_targets", detail=detail)

        ordered = sorted(candidates, key=self._sort_key)
        selected: list[_Candidate] = []
        selected_target_ids: set[str] = set()
        for candidate in ordered:
            duplicate_distance = self._duplicate_distance(candidate, selected)
            if duplicate_distance is not None:
                rejected.append(
                    {
                        "id": candidate.id,
                        "class_name": candidate.class_name,
                        "reason": "duplicate_near_selected",
                        "distance_m": duplicate_distance,
                    }
                )
                continue
            if self.coordinate_mode == "gps_enu":
                base_target_id = candidate.target_id or f"gps_target_{candidate.index}"
                unique_target_id = base_target_id
                if unique_target_id in selected_target_ids:
                    unique_target_id = f"{base_target_id}_{candidate.index}"
                suffix = 2
                while unique_target_id in selected_target_ids:
                    unique_target_id = f"{base_target_id}_{candidate.index}_{suffix}"
                    suffix += 1
                candidate.target_id = unique_target_id
                selected_target_ids.add(unique_target_id)
            selected.append(candidate)
            if len(selected) >= self.target_count:
                break

        if len(selected) < self.target_count and not self.allow_fewer:
            detail = self._base_detail(selected, rejected, candidate_count=len(candidates))
            return ActionResult(failed=True, reason="not_enough_drop_targets", detail=detail)

        detail = self._base_detail(selected, rejected, candidate_count=len(candidates))
        return ActionResult(done=True, reason="drop_targets_selected", output=self._output(detail), detail=detail)

    def _candidate(
        self,
        obj: dict[str, Any],
        index: int,
    ) -> tuple[_Candidate | None, dict[str, Any] | None]:
        raw_target_id = obj.get("target_id")
        target_id = str(raw_target_id).strip() if raw_target_id is not None else ""
        if target_id.lower() in {"", "none", "null"}:
            target_id = ""
        fallback_id = f"gps_target_{index}" if self.coordinate_mode == "gps_enu" else f"target_{index}"
        raw_object_id = obj.get("id") or target_id or fallback_id
        object_id = str(raw_object_id).strip()
        if object_id.lower() in {"", "none", "null"}:
            object_id = fallback_id
        if self.coordinate_mode == "gps_enu" and not target_id:
            target_id = object_id
        candidate_target_id: str | None = target_id or None
        class_name = str(obj.get("class_name") or obj.get("label") or "bucket")
        base_rejection = {"id": object_id, "class_name": class_name}

        xy = self._xy(obj)
        if xy is None:
            return None, {**base_rejection, "reason": "missing_xy"}
        local_x, local_y = xy
        if not math.isfinite(local_x) or not math.isfinite(local_y):
            return None, {**base_rejection, "reason": "invalid_xy"}
        # GPS mode: also validate lat/lon
        if self.coordinate_mode == "gps_enu":
            lat = obj.get("lat"); lon = obj.get("lon")
            if lat is None or lon is None:
                return None, {**base_rejection, "reason": "missing_gps"}
            try:
                lat_f = float(lat); lon_f = float(lon)
                if not math.isfinite(lat_f) or not math.isfinite(lon_f):
                    return None, {**base_rejection, "reason": "invalid_gps"}
                if lat_f < -90 or lat_f > 90 or lon_f < -180 or lon_f > 180:
                    return None, {**base_rejection, "reason": "invalid_gps"}
            except (TypeError, ValueError):
                return None, {**base_rejection, "reason": "invalid_gps"}

        seen_count = self._int_value(
            obj.get("seen_count", obj.get("count", obj.get("sample_count", obj.get("raw_count", 0)))),
            default=0,
        )
        raw_count = self._int_value(obj.get("raw_count", seen_count), default=seen_count)
        weight = self._float_value(obj.get("weight", 0.0), default=0.0)
        if seen_count < self.min_seen_count:
            return None, {**base_rejection, "reason": "low_seen_count"}
        if raw_count < self.min_raw_count:
            return None, {**base_rejection, "reason": "low_raw_count"}
        if weight < self.min_weight:
            return None, {**base_rejection, "reason": "low_weight"}
        if class_name not in self.score_table:
            return None, {**base_rejection, "reason": "unknown_class"}

        order_index = self.prefer_class_order.index(class_name) if class_name in self.prefer_class_order else len(self.prefer_class_order)
        zone_distance_m = self._zone_distance(local_x, local_y)
        return (
            _Candidate(
                original=obj,
                index=index,
                id=object_id,
                target_id=candidate_target_id,
                class_name=class_name,
                local_x=local_x,
                local_y=local_y,
                score=self.score_table[class_name],
                seen_count=seen_count,
                raw_count=raw_count,
                weight=weight,
                order_index=order_index,
                zone_distance_m=zone_distance_m,
            ),
            None,
        )

    def _base_detail(
        self,
        selected: list[_Candidate],
        rejected: list[dict[str, Any]],
        *,
        candidate_count: int | None = None,
    ) -> dict[str, Any]:
        selected_targets = [self._selected_dict(candidate, rank) for rank, candidate in enumerate(selected, start=1)]
        detail = {
            "selected_targets": selected_targets,
            "candidate_count": len(selected) if candidate_count is None else candidate_count,
            "selected_count": len(selected_targets),
            "rejected_count": len(rejected),
            "rejected_objects": rejected,
            "score_table": dict(self.score_table),
            "target_count": self.target_count,
            "allow_fewer": self.allow_fewer,
            "key": self.key,
        }
        outputs = (
            self.single_target_servo_outputs
            if len(selected_targets) <= 1
            else self.multi_target_first_servo_outputs
        )
        detail["first_release_servo_outputs"] = [dict(item) for item in (outputs or [])]
        detail["first_alignment_enabled"] = len(selected_targets) >= 1
        detail["second_alignment_enabled"] = len(selected_targets) >= 2
        detail["second_release_enabled"] = len(selected_targets) >= 2
        detail["target_slots"] = self._build_target_slots(selected)
        return detail

    def _build_target_slots(self, selected: list[_Candidate]) -> list[dict[str, Any]]:
        slots: list[dict[str, Any]] = []
        for rank_index in range(self.target_count):
            if rank_index < len(selected):
                candidate = selected[rank_index]
                gps_lat, gps_lon = self._gps_from_original(candidate.original)
                slot: dict[str, Any] = {
                    "valid": True,
                    "id": candidate.id,
                    "target_id": candidate.target_id,
                    "class_name": candidate.class_name,
                    "local_x": candidate.local_x,
                    "local_y": candidate.local_y,
                    "x": candidate.local_x,
                    "y": candidate.local_y,
                    "east_m": candidate.local_x,
                    "north_m": candidate.local_y,
                    "score": candidate.score,
                    "seen_count": candidate.seen_count,
                    "count": candidate.seen_count,
                    "raw_count": candidate.raw_count,
                    "weight": candidate.weight,
                    "track_ids": list(candidate.original.get("track_ids") or []),
                    "rank": rank_index + 1,
                }
                if gps_lat is not None:
                    slot["lat"] = gps_lat
                if gps_lon is not None:
                    slot["lon"] = gps_lon
                slots.append(slot)
            elif rank_index == 0 and not selected and self.fallback_target is not None:
                slots.append(dict(self.fallback_target))
            else:
                missing_target_id = (
                    f"gps_target_missing_{rank_index}"
                    if self.coordinate_mode == "gps_enu"
                    else None
                )
                slots.append({
                    "valid": False,
                    "id": f"missing_drop_target_{rank_index}",
                    "target_id": missing_target_id,
                    "class_name": "",
                    "local_x": None,
                    "local_y": None,
                    "x": None,
                    "y": None,
                    "lat": None,
                    "lon": None,
                    "score": 0.0,
                    "seen_count": 0,
                    "count": 0,
                    "raw_count": 0,
                    "weight": 0.0,
                    "track_ids": [],
                    "rank": rank_index + 1,
                    "status": "missing",
                })
        return slots

    @staticmethod
    def _fallback_target(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("fallback_target must be an object")
        try:
            field_x_m = float(value["field_x_m"] if "field_x_m" in value else value["x"])
            field_y_m = float(value["field_y_m"] if "field_y_m" in value else value["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("fallback_target requires finite field x/y") from exc
        if not math.isfinite(field_x_m) or not math.isfinite(field_y_m):
            raise ValueError("fallback_target requires finite field x/y")
        target_id = str(value.get("target_id") or "drop_zone_center")
        return {
            "valid": True,
            "id": str(value.get("id") or target_id),
            "target_id": target_id,
            "class_name": str(value.get("class_name") or ""),
            "x": field_x_m,
            "y": field_y_m,
            "lat": None,
            "lon": None,
            "score": 0.0,
            "seen_count": 0,
            "count": 0,
            "raw_count": 0,
            "weight": 0.0,
            "track_ids": [],
            "rank": 1,
            "status": "fallback_center",
        }

    @staticmethod
    def _bool_param(value: Any, name: str) -> bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"{name} must be a bool")

    @staticmethod
    def _servo_outputs(value: Any, name: str) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        if not isinstance(value, list) or not value:
            raise ValueError(f"{name} must be a non-empty list")
        if not all(isinstance(item, dict) for item in value):
            raise ValueError(f"{name} entries must be dicts")
        return [dict(item) for item in value]

    @staticmethod
    def _gps_from_original(obj: dict[str, Any]) -> tuple[float | None, float | None]:
        """Extract lat/lon from an input object, with gps_lat/gps_lon fallback."""
        lat = obj.get("lat")
        lon = obj.get("lon")
        if lat is None:
            lat = obj.get("gps_lat")
        if lon is None:
            lon = obj.get("gps_lon")
        try:
            lat_f = None if lat is None else float(lat)
        except (TypeError, ValueError):
            lat_f = None
        try:
            lon_f = None if lon is None else float(lon)
        except (TypeError, ValueError):
            lon_f = None
        if lat_f is not None and not math.isfinite(lat_f):
            lat_f = None
        if lon_f is not None and not math.isfinite(lon_f):
            lon_f = None
        return lat_f, lon_f

    def _selected_dict(self, candidate: _Candidate, rank: int) -> dict[str, Any]:
        data = {
            "id": candidate.id,
            "target_id": candidate.target_id,
            "class_name": candidate.class_name,
            "local_x": candidate.local_x,
            "local_y": candidate.local_y,
            "x": candidate.local_x,
            "y": candidate.local_y,
            "score": candidate.score,
            "seen_count": candidate.seen_count,
            "count": candidate.seen_count,
            "raw_count": candidate.raw_count,
            "weight": candidate.weight,
            "track_ids": list(candidate.original.get("track_ids") or []),
            "rank": rank,
        }
        if self.coordinate_mode == "gps_enu":
            data["east_m"] = candidate.local_x
            data["north_m"] = candidate.local_y
        gps_lat, gps_lon = self._gps_from_original(candidate.original)
        if gps_lat is not None:
            data["lat"] = gps_lat
        if gps_lon is not None:
            data["lon"] = gps_lon
        if "local_z" in candidate.original:
            data["local_z"] = candidate.original["local_z"]
        elif "z" in candidate.original:
            data["local_z"] = candidate.original["z"]
        return data

    def _sort_key(self, candidate: _Candidate) -> tuple[float, int, int, int, float, float, int]:
        zone_distance = candidate.zone_distance_m if candidate.zone_distance_m is not None else 0.0
        return (
            -candidate.score,
            candidate.order_index,
            -candidate.seen_count,
            -candidate.raw_count,
            -candidate.weight,
            zone_distance,
            candidate.index,
        )

    def _duplicate_distance(self, candidate: _Candidate, selected: list[_Candidate]) -> float | None:
        if self.deduplicate_radius_m == 0.0:
            return None
        for item in selected:
            distance = math.hypot(candidate.local_x - item.local_x, candidate.local_y - item.local_y)
            if distance < self.deduplicate_radius_m:
                return distance
        return None

    def _xy(self, obj: dict[str, Any]) -> tuple[float, float] | None:
        if self.coordinate_mode == "gps_enu":
            east = obj.get("east_m")
            north = obj.get("north_m")
            if east is not None and north is not None:
                try:
                    return (float(east), float(north))
                except (TypeError, ValueError):
                    return None
            return None
        x_value = obj.get("local_x")
        y_value = obj.get("local_y")
        if x_value is None or y_value is None:
            if self.require_local_xy:
                x_value = obj.get("x")
                y_value = obj.get("y")
            else:
                x_value = obj.get("x", x_value)
                y_value = obj.get("y", y_value)
        if x_value is None or y_value is None:
            return None
        try:
            return float(x_value), float(y_value)
        except (TypeError, ValueError):
            return float("nan"), float("nan")

    def _zone_center(self, value: Any) -> tuple[float, float] | None:
        if not isinstance(value, dict):
            return None
        try:
            x = float(value["x"])
            y = float(value["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        return x, y

    def _zone_distance(self, x: float, y: float) -> float | None:
        effective = getattr(self, '_effective_zone_center', None)
        if effective is None:
            return None
        return math.hypot(x - effective[0], y - effective[1])

    def _clone_result(self, result: ActionResult | None) -> ActionResult:
        if result is None:
            return ActionResult(failed=True, reason="missing_cached_result")
        return ActionResult(
            effects=ActionResult.typed(list(result.actions)),
            done=result.done,
            failed=result.failed,
            reason=result.reason,
            output=dict(result.output),
            detail=dict(result.detail),
        )

    @staticmethod
    def _output(detail: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_targets": list(detail.get("selected_targets") or []),
            "target_slots": list(detail.get("target_slots") or []),
            "selected_count": int(detail.get("selected_count", 0)),
            "candidate_count": int(detail.get("candidate_count", 0)),
            "first_release_servo_outputs": list(detail.get("first_release_servo_outputs") or []),
            "first_alignment_enabled": bool(detail.get("first_alignment_enabled")),
            "second_alignment_enabled": bool(detail.get("second_alignment_enabled")),
            "second_release_enabled": bool(detail.get("second_release_enabled")),
        }

    def _bool_param(self, value: Any, name: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError(f"{name} must be a bool")

    def _int_value(self, value: Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _float_value(self, value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float_context(context: dict[str, Any], name: str) -> float | None:
        value = context.get(name)
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None
