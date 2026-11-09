"""Internal schema-v3 calibration session for :mod:`field.service`.

The orchestrator owns sampling and the Service/RuntimeContext transaction.  It
does not read hardware, use wall-clock time, expose Web APIs, or send commands.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import fields, replace
from typing import Mapping

from .profile import FieldProfile
from ._reference_store import _ReferenceStore
from field.context import RuntimeContextBuilder
from .calibration import (
    RuntimeFieldBindingCandidate,
    RuntimeFieldBindingSampler,
)
from .geometry import RuntimeFieldGeometry, RuntimeFieldPoint
from contracts.platform.field import ReferenceVersion


class CalibrationSession:
    """Own one runtime sampling session and its atomic reference apply."""

    def __init__(
        self,
        svc: _ReferenceStore,
        builder: RuntimeContextBuilder,
    ) -> None:
        self._svc = svc
        self._builder = builder
        self._last_observed_at_s: float | None = None
        self._sampler: RuntimeFieldBindingSampler | None = None
        self._profile: FieldProfile | None = None
        self._profile_name: str | None = None
        self._candidate: RuntimeFieldBindingCandidate | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._last_result: dict[str, object] | None = None
        # preview / competition session metadata
        self._preview_candidate: RuntimeFieldBindingCandidate | None = None
        self._preview_error: str | None = None
        self._template_profile_id: str | None = None
        self._runtime_profile_id: str | None = None
        self._input_source: str | None = None
        self._forward_marker_lat: float | None = None
        self._forward_marker_lon: float | None = None
        self._session_id: str | None = None
        self._session_revision = 0
        self._base_version: ReferenceVersion | None = None
        self._auto_commit = True
        self._heading_mode: str = "forward_marker"
        self._operation_results: dict[str, dict[str, object]] = {}
        self._observation_results: dict[str, dict[str, object]] = {}

    def start(
        self,
        profile: FieldProfile,
        *,
        started_at_s: float,
        template_profile_id: str | None = None,
        runtime_profile_id: str | None = None,
        input_source: str | None = None,
        forward_marker_lat: float | None = None,
        forward_marker_lon: float | None = None,
        operation_id: str | None = None,
        auto_commit: bool = True,
        heading_mode: str = "forward_marker",
    ) -> dict[str, object]:
        operation_id = operation_id or uuid.uuid4().hex
        if operation_id in self._operation_results:
            return dict(self._operation_results[operation_id]) | {"replayed": True}
        if self._state != "idle":
            return {
                "ok": False,
                "state": self._state,
                "error": (
                    f"cannot start runtime binding in state {self._state}; "
                    + (
                        "use field reference reset"
                        if self._state == "applied"
                        else "cancel or reset the current session first"
                    )
                ),
            }
        if self._svc.reference.is_frozen:
            return {
                "ok": False,
                "state": self._state,
                "error": "field reference is frozen; reset first",
            }
        if not isinstance(profile, FieldProfile):
            return self._failure("profile must be a FieldProfile", state=self._state)
        if not _finite_number(started_at_s):
            return self._failure("started_at_s must be finite", state=self._state)
        if profile.schema_version != 3:
            return self._failure(
                "runtime GPS sampling requires schema v3", state=self._state
            )
        if heading_mode not in ("forward_marker", "rtk"):
            return self._failure(
                f"heading_mode must be 'forward_marker' or 'rtk', got {heading_mode!r}",
                state=self._state,
            )
        try:
            sampler = RuntimeFieldBindingSampler(profile, heading_mode=heading_mode)
            sampling = sampler.start(started_at_s=float(started_at_s))
        except Exception as exc:
            return self._failure(exc, state=self._state)

        self._sampler = sampler
        self._profile = profile
        self._profile_name = profile.name
        self._candidate = None
        self._state = "sampling"
        self._heading_mode = heading_mode
        self._last_observed_at_s = float(started_at_s)
        self._last_error = None
        self._last_result = None
        self._preview_candidate = None
        self._preview_error = None
        self._template_profile_id = template_profile_id
        self._runtime_profile_id = runtime_profile_id
        self._input_source = input_source
        self._forward_marker_lat = forward_marker_lat
        self._forward_marker_lon = forward_marker_lon
        self._session_id = uuid.uuid4().hex
        self._session_revision = 1
        self._base_version = self._svc.current_version()
        self._auto_commit = bool(auto_commit)
        result = {
            "ok": True,
            "profile_id": profile.profile_id,
            "state": "sampling",
            "heading_mode": heading_mode,
            "sampling": _status_dict(sampling),
            "session_id": self._session_id,
            "session_revision": self._session_revision,
            "base_version": self._version_dict(self._base_version),
        }
        self._remember(self._operation_results, operation_id, result)
        return result

    def observe(
        self,
        snapshot: object,
        *,
        observed_at_s: float,
        observation_id: str | None = None,
        expected_session_revision: int | None = None,
    ) -> dict[str, object]:
        observation_id = observation_id or uuid.uuid4().hex
        if observation_id in self._observation_results:
            return dict(self._observation_results[observation_id]) | {"replayed": True}
        if expected_session_revision is not None and expected_session_revision != self._session_revision:
            return self._failure("stale_session_revision", state=self._state)
        if self._base_version is not None and not self._svc.version_matches(self._base_version):
            return self._failure("base_reference_version_changed", state="apply_failed")
        if self._sampler is None or self._state != "sampling":
            return {"ok": True, "observed": False, "state": self._state}
        if not _finite_number(observed_at_s):
            return self._failure(
                "observed_at_s must be finite", state=self._state
            )
        self._last_observed_at_s = float(observed_at_s)
        try:
            sampling = self._sampler.observe_snapshot(
                snapshot, observed_at_s=float(observed_at_s)  # type: ignore[arg-type]
            )
        except Exception as exc:
            sampler_state = self._safe_sampler_state()
            state = "sampling_failed" if sampler_state == "failed" else "sampling"
            return self._failure(exc, state=state, observed=True)
        if sampling.state == "failed":
            return self._failure(
                "runtime sampler entered failed state",
                state="sampling_failed",
                observed=True,
            )
        # Completing a valid sampling window is the confirmation point for
        # schema-v3 runtime GPS binding.  There is deliberately no second
        # operator action between a passed sample set and freezing the field
        # reference: the transaction below still validates, applies, syncs,
        # and freezes atomically.
        if sampling.window_complete and sampling.can_finalize and self._auto_commit:
            result = self.finalize(completed_at_s=float(observed_at_s))
            result["observed"] = True
            result["auto_finalized"] = True
            self._remember(self._observation_results, observation_id, result)
            return result
        # Try to generate a preview after the sampling window completes
        self._try_preview(observed_at_s=float(observed_at_s))
        result = {
            "ok": True,
            "observed": True,
            "state": "sampling",
            "sampling": _status_dict(sampling),
        }
        self._session_revision += 1
        result["session_revision"] = self._session_revision
        self._remember(self._observation_results, observation_id, result)
        return result

    def finalize(self, *, completed_at_s: float, operation_id: str | None = None,
                 expected_session_revision: int | None = None) -> dict[str, object]:
        operation_id = operation_id or uuid.uuid4().hex
        if operation_id in self._operation_results:
            return dict(self._operation_results[operation_id]) | {"replayed": True}
        if expected_session_revision is not None and expected_session_revision != self._session_revision:
            return self._failure("stale_session_revision", state=self._state)
        if self._base_version is not None and not self._svc.version_matches(self._base_version):
            return self._failure("base_reference_version_changed", state="apply_failed")
        if self._state == "applied" and self._last_result is not None:
            return {
                "ok": False,
                "state": "applied",
                "error": "runtime binding is already applied; use field reference reset",
            }
        if not _finite_number(completed_at_s):
            return self._failure("completed_at_s must be finite", state=self._state)
        if self._sampler is None:
            return self._failure("no runtime sampling session", state=self._state)
        if self._state == "apply_failed" and self._candidate is not None:
            return self._apply(self._candidate, completed_at_s=float(completed_at_s), operation_id=operation_id)
        if self._state != "sampling":
            return self._failure(
                f"cannot finalize runtime binding in state {self._state}",
                state=self._state,
            )

        try:
            sampling = self._sampler.status(now_s=float(completed_at_s))
        except Exception as exc:
            return self._failure(exc, state="sampling")
        if sampling.state == "failed":
            return self._failure(
                "runtime sampler is failed", state="sampling_failed"
            )
        if not sampling.window_complete:
            return self._failure(
                "sampling window not yet complete", state="sampling"
            )
        if not sampling.can_finalize:
            return self._failure(
                "sampling cannot finalize — insufficient samples or other issue",
                state="sampling",
            )

        try:
            candidate = self._sampler.finalize(
                completed_at_s=float(completed_at_s)
            )
        except Exception as exc:
            state = (
                "sampling_failed"
                if self._safe_sampler_state() == "failed"
                else "sampling"
            )
            return self._failure(exc, state=state)

        self._candidate = candidate
        self._state = "candidate_ready"
        self._last_error = None
        # Clear preview on formal finalize
        self._preview_candidate = None
        self._preview_error = None
        return self._apply(candidate, completed_at_s=float(completed_at_s), operation_id=operation_id)

    def cancel(self, operation_id: str | None = None) -> dict[str, object]:
        operation_id = operation_id or uuid.uuid4().hex
        if operation_id in self._operation_results:
            return dict(self._operation_results[operation_id]) | {"replayed": True}
        if self._state == "applied":
            return {
                "ok": False,
                "state": "applied",
                "error": (
                    "runtime binding is already applied; "
                    "use field reference reset"
                ),
            }
        session_id = self._session_id
        result = self.reset() | {"session_id": session_id, "operation_id": operation_id}
        self._remember(self._operation_results, operation_id, result)
        return result

    def reset(self) -> dict[str, object]:
        """Unconditionally discard the complete orchestrator lifecycle."""
        if self._sampler is not None:
            try:
                self._sampler.reset()
            except Exception:
                # Cancellation still discards the owned sampler; it does not
                # claim that a state restore transaction succeeded.
                pass
        self._sampler = None
        self._profile = None
        self._profile_name = None
        self._candidate = None
        self._state = "idle"
        self._last_observed_at_s = None
        self._last_error = None
        self._last_result = None
        self._preview_candidate = None
        self._preview_error = None
        self._template_profile_id = None
        self._runtime_profile_id = None
        self._input_source = None
        self._forward_marker_lat = None
        self._forward_marker_lon = None
        self._session_id = None
        self._session_revision = 0
        self._base_version = None
        self._heading_mode = "forward_marker"
        self._observation_results.clear()
        return {"ok": True, "state": "idle"}

    def _apply(
        self,
        candidate: RuntimeFieldBindingCandidate,
        *,
        completed_at_s: float,
        operation_id: str,
    ) -> dict[str, object]:
        if self._base_version is None or self._session_id is None:
            return self._failure("calibration session identity unavailable", state="apply_failed")
        receipt = self._svc.commit_calibration(
            candidate, profile_name=self._profile_name or "runtime",
            session_id=self._session_id, operation_id=operation_id,
            expected_version=self._base_version, timestamp=completed_at_s,
        )
        if not receipt.accepted:
            return self._failure(receipt.reason_code, state="apply_failed")

        # RuntimeContextBuilder is a projection consumer, not a transaction
        # participant or rollback source.
        projection_error: str | None = None
        try:
            self._builder.bind_field_reference_snapshot(self._svc.snapshot())
        except Exception as exc:
            # Projection is downstream of the atomic repository commit.  It
            # must never roll the committed reference back or create a second
            # writer path.
            projection_error = str(exc)

        self._state = "applied"
        self._last_error = None
        geometry = _geometry_payload(candidate.geometry)
        applied: dict[str, object] = {
            "ok": True,
            "state": "applied",
            "profile_id": candidate.profile_id,
            "synced_to_runtime": True,
            "is_frozen": True,
            "is_ready_for_field_to_gps": True,
            "origin_lat": candidate.origin_lat,
            "origin_lon": candidate.origin_lon,
            "forward_marker_lat": candidate.forward_marker_lat,
            "forward_marker_lon": candidate.forward_marker_lon,
            "field_heading_yaw_rad": candidate.field_heading_yaw_rad,
            "field_heading_deg": candidate.field_heading_deg,
            "baseline_m": candidate.baseline_m,
            "sample_count": candidate.sample_count,
            "horizontal_spread_m": candidate.horizontal_spread_m,
            "warnings": list(candidate.warnings),
            "geometry": geometry,
            "session_id": self._session_id,
            "reference_version": self._version_dict(receipt.current_version),
            "operation_id": operation_id,
        }
        if projection_error is not None:
            applied["projection_error"] = projection_error
        self._last_result = applied
        self._remember(self._operation_results, operation_id, applied)
        return dict(applied)

    @staticmethod
    def _version_dict(version: ReferenceVersion) -> dict[str, object]:
        return {"generation_id": version.generation_id, "revision": version.revision}

    @staticmethod
    def _remember(target: dict[str, dict[str, object]], key: str,
                  value: dict[str, object], capacity: int = 512) -> None:
        target[key] = dict(value)
        while len(target) > capacity:
            target.pop(next(iter(target)))

    @property
    def state(self) -> str:
        return self._state

    def synced_to_runtime(
        self,
        service_status: Mapping[str, object] | None = None,
        *,
        require_frozen: bool = True,
    ) -> bool:
        """Return whether the single reference matches the applied candidate."""
        if self._candidate is None:
            return False
        try:
            status = service_status if service_status is not None else self._svc.status()
            return _reference_matches_candidate(self._candidate, status, require_frozen=require_frozen)
        except Exception:
            return False

    def _failure(
        self,
        error: object,
        *,
        state: str,
        rollback_ok: bool | None = None,
        observed: bool | None = None,
    ) -> dict[str, object]:
        self._state = state
        self._last_error = str(error)
        result: dict[str, object] = {
            "ok": False,
            "state": state,
            "error": str(error),
        }
        if rollback_ok is not None:
            result["rollback_ok"] = rollback_ok
        if observed is not None:
            result["observed"] = observed
        profile_id = (
            self._candidate.profile_id
            if self._candidate is not None
            else self._profile.profile_id if self._profile is not None else None
        )
        if profile_id is not None:
            result["profile_id"] = profile_id
        self._last_result = dict(result)
        return result

    def _try_preview(self, *, observed_at_s: float) -> None:
        """Attempt to generate a preview candidate after window completion.

        Only runs when sampler state is still 'sampling' (not already failed).
        On failure, sets ``_preview_error`` and optionally transitions to
        ``sampling_failed``.  On success, stores ``_preview_candidate``.
        """
        if self._sampler is None or self._state != "sampling":
            return
        try:
            sampling = self._sampler.status(now_s=observed_at_s)
        except Exception:
            return
        if not sampling.window_complete:
            return
        # Try preview
        try:
            candidate = self._sampler.preview_candidate(
                completed_at_s=observed_at_s
            )
        except Exception as exc:
            error_msg = str(exc)
            self._preview_candidate = None
            self._preview_error = error_msg
            # Determine failure state transition
            if "accepted samples" in error_msg.lower() and "< required" in error_msg.lower():
                self._state = "sampling_failed"
                self._last_error = "accepted samples below required minimum"
            elif "horizontal spread" in error_msg.lower() and "exceeds" in error_msg.lower():
                self._state = "sampling_failed"
                self._last_error = "horizontal spread exceeded"
            elif "baseline" in error_msg.lower() and ("below" in error_msg.lower() or "minimum" in error_msg.lower()):
                self._state = "sampling_failed"
                self._last_error = "baseline below minimum"
            return

        # Check baseline policy (skip for RTK heading mode — no physical baseline)
        warnings: list[str] = list(candidate.warnings)
        baseline_m = candidate.baseline_m
        profile = self._profile
        if profile is not None and self._heading_mode != "rtk":
            min_baseline = profile.binding_policy.min_baseline_m
            if baseline_m < min_baseline and not math.isclose(
                baseline_m, min_baseline, rel_tol=0.0, abs_tol=1e-6
            ):
                self._preview_candidate = None
                self._preview_error = "baseline below minimum"
                self._state = "sampling_failed"
                self._last_error = (
                    f"baseline {baseline_m:.1f}m < minimum {min_baseline:.1f}m"
                )
                return
            warn_below = profile.binding_policy.warn_baseline_below_m
            if baseline_m < warn_below and not math.isclose(
                baseline_m, warn_below, rel_tol=0.0, abs_tol=1e-6
            ):
                warning_msg = (
                    f"A→B baseline {baseline_m:.1f} m is below warning threshold "
                    f"{warn_below:.1f} m (minimum {min_baseline:.1f} m)"
                )
                if warning_msg not in warnings:
                    warnings.append(warning_msg)

        # Success — store preview (does NOT modify sampler state)
        self._preview_candidate = replace(candidate, warnings=tuple(warnings))
        self._preview_error = None

    def _safe_sampler_state(self) -> str | None:
        if self._sampler is None:
            return None
        try:
            return self._sampler.status(now_s=self._last_observed_at_s).state
        except Exception:
            return None

    def status(self, *, now_s: float | None = None) -> dict[str, object]:
        sampling: dict[str, object] | None = None
        if self._sampler is not None and self._state in ("sampling", "sampling_failed", "apply_failed"):
            timestamp = now_s if now_s is not None else self._last_observed_at_s
            try:
                raw_status = self._sampler.status(now_s=timestamp)
                sampling = _status_dict(raw_status)
                # Override can_finalize with preview-based check
                if self._preview_candidate is not None and self._state == "sampling":
                    sampling["can_finalize"] = True
                else:
                    sampling["can_finalize"] = False
            except Exception as exc:
                sampling = {"state": self._state, "error": str(exc)}

        # Use preview candidate for summary/geometry if available (before finalize)
        candidate_summary: dict[str, object] | None = None
        geometry: dict[str, object] | None = None
        effective_candidate = self._candidate or self._preview_candidate
        if effective_candidate is not None:
            summary: dict[str, object] = {
                "origin_lat": effective_candidate.origin_lat,
                "origin_lon": effective_candidate.origin_lon,
                "forward_marker_lat": effective_candidate.forward_marker_lat,
                "forward_marker_lon": effective_candidate.forward_marker_lon,
                "field_heading_yaw_rad": effective_candidate.field_heading_yaw_rad,
                "field_heading_deg": effective_candidate.field_heading_deg,
                "baseline_m": effective_candidate.baseline_m,
                "sample_count": effective_candidate.sample_count,
                "rejected_sample_count": effective_candidate.rejected_sample_count,
                "duplicate_sample_count": effective_candidate.duplicate_sample_count,
                "horizontal_spread_m": effective_candidate.horizontal_spread_m,
                "gps_fix_type": effective_candidate.gps_fix_type,
                "gps_satellites": effective_candidate.gps_satellites,
                "gps_eph": effective_candidate.gps_eph,
                "gps_epv": effective_candidate.gps_epv,
                "warnings": list(effective_candidate.warnings),
            }
            candidate_summary = summary
            geometry = _geometry_payload(effective_candidate.geometry)

        return {
            "state": self._state,
            "template_profile_id": self._template_profile_id,
            "runtime_profile_id": self._runtime_profile_id,
            "profile_id": (
                self._candidate.profile_id
                if self._candidate is not None
                else self._profile.profile_id if self._profile is not None else None
            ),
            "profile_name": self._profile_name,
            "input_source": self._input_source,
            "forward_marker_lat": self._forward_marker_lat,
            "forward_marker_lon": self._forward_marker_lon,
            "last_error": self._last_error,
            "preview_error": self._preview_error,
            "candidate_ready": self._candidate is not None,
            "sampling": sampling,
            "candidate_summary": candidate_summary,
            "geometry": geometry,
            "last_result": dict(self._last_result) if self._last_result else None,
        }


def _reference_matches_candidate(
    candidate: RuntimeFieldBindingCandidate,
    service_status: Mapping[str, object],
    *,
    require_frozen: bool,
) -> bool:
    """Verify the one FieldReference against its calibration candidate."""
    if service_status.get("is_confirmed") is not True:
        return False
    if service_status.get("is_ready_for_field_to_gps") is not True:
        return False
    if service_status.get("origin_source") != candidate.origin_source:
        return False
    if service_status.get("heading_source") != candidate.heading_source:
        return False
    if service_status.get("profile_id") != candidate.profile_id:
        return False
    if require_frozen and service_status.get("is_frozen") is not True:
        return False
    for actual, expected in (
        (service_status.get("origin_lat"), candidate.origin_lat),
        (service_status.get("origin_lon"), candidate.origin_lon),
        (service_status.get("forward_marker_lat"), candidate.forward_marker_lat),
        (service_status.get("forward_marker_lon"), candidate.forward_marker_lon),
        (service_status.get("field_heading_yaw_rad"), candidate.field_heading_yaw_rad),
    ):
        if not _same_float(actual, expected):
            return False

    return True


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _same_float(actual: object, expected: object) -> bool:
    return (
        _finite_number(actual)
        and _finite_number(expected)
        and math.isclose(
            float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9
        )
    )


def _same_int(actual: object, expected: object) -> bool:
    return (
        isinstance(actual, int)
        and not isinstance(actual, bool)
        and isinstance(expected, int)
        and not isinstance(expected, bool)
        and actual == expected
    )


def _status_dict(status: object) -> dict[str, object]:
    return {item.name: getattr(status, item.name) for item in fields(status)}


def _point_payload(point: RuntimeFieldPoint) -> dict[str, object]:
    return {
        "name": point.name,
        "field_x_m": point.field_x_m,
        "field_y_m": point.field_y_m,
        "altitude_m": point.altitude_m,
        "lat": point.lat,
        "lon": point.lon,
    }


def _geometry_payload(geometry: RuntimeFieldGeometry) -> dict[str, object]:
    return {
        "home": _point_payload(geometry.home),
        "forward_marker": _point_payload(geometry.forward_marker),
        "drop_scan_waypoints": [
            _point_payload(point) for point in geometry.drop_scan_waypoints
        ],
        "recon_scan_waypoints": [
            _point_payload(point) for point in geometry.recon_scan_waypoints
        ],
        "drop_area_corners": [
            _point_payload(point) for point in geometry.drop_area_corners
        ],
        "recce_area_corners": [
            _point_payload(point) for point in geometry.recce_area_corners
        ],
        "heading": {
            "yaw_rad": geometry.field_heading_yaw_rad,
            "degrees": geometry.field_heading_deg,
        },
        "baseline": geometry.baseline_m,
        "warnings": list(geometry.warnings),
    }
