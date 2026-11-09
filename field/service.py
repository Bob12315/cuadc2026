"""Schema-v3 runtime Field Reference controller."""
from __future__ import annotations

import math
import os
from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping

from field.profile import FieldProfile, validate_field_profile
from field.profile_service import ReadOnlyFieldProfileRepository
from field.models import validate_wgs84_lat_lon
from field._reference_store import _ReferenceStore
from field.calibration_session import CalibrationSession
from field.context import RuntimeContextBuilder
from field.calibration_transaction import FieldCalibrationTransactionAdapter
from contracts.platform.field import CalibrationMode, CalibrationStart, GpsObservation
import uuid


class FieldService:
    def __init__(self, runtime_context_builder: RuntimeContextBuilder,
                 reference_store: _ReferenceStore | None = None,
                 profile_repository: ReadOnlyFieldProfileRepository | None = None) -> None:
        self._svc = reference_store or _ReferenceStore()
        if profile_repository is None:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            profile_repository = ReadOnlyFieldProfileRepository((("config", os.path.join(root, "config", "field_profiles")),
                                                                  ("runtime", os.path.join(root, "runtime", "field_profiles"))))
        self.profile_repository = profile_repository
        self._builder = runtime_context_builder
        self._builder.bind_field_reference_snapshot(self._svc.snapshot())
        self._runtime_binding = CalibrationSession(self._svc, runtime_context_builder)
        self.calibration = FieldCalibrationTransactionAdapter(self._runtime_binding, self.profile_repository)

    @property
    def reference(self):
        return self._svc.reference

    def status(self) -> dict[str, object]:
        state = self._svc.status()
        yaw = state.get("field_heading_yaw_rad")
        active = state.get("origin_source") == "runtime_current_gps"
        return {
            "ok": True,
            "field_reference": {
                "is_confirmed": state["is_confirmed"], "is_frozen": state["is_frozen"],
                "version": state["version"],
                "is_ready": self._svc.reference.is_ready(),
                "is_ready_for_field_to_gps": self._svc.reference.is_ready_for_field_to_gps(),
                "origin_source": state["origin_source"], "heading_source": state["heading_source"],
                "origin_lat": state["origin_lat"], "origin_lon": state["origin_lon"],
                "forward_marker_lat": state.get("forward_marker_lat"), "forward_marker_lon": state.get("forward_marker_lon"),
                "field_heading_yaw_rad": yaw,
                "field_heading_deg": math.degrees(float(yaw)) if yaw is not None else None,
                "active_source": ("runtime_origin_forward_marker" if state.get("heading_source") == "runtime_forward_marker"
                                  else "runtime_origin_rtk_heading" if state.get("heading_source") == "runtime_rtk_heading"
                                  else "none") if active else "none",
                "synced_to_runtime": self._runtime_binding.synced_to_runtime(state, require_frozen=True) if active else False,
                "profile_id": self._svc.snapshot().profile_id,
                "runtime_binding": self._runtime_binding.status(),
                "warnings": [],
            },
        }

    def reset(self) -> dict[str, object]:
        self._runtime_binding.reset()
        result = self._svc.reset()
        self._builder.bind_field_reference_snapshot(self._svc.snapshot())
        return result

    def freeze(self) -> dict[str, object]:
        return self._svc.freeze()

    def start_runtime_profile_sampling(self, profile_id: str, *, started_at_s: float) -> dict[str, object]:
        operation_id=uuid.uuid4().hex
        receipt=self.calibration.start(CalibrationStart(operation_id, CalibrationMode.REGISTERED_PROFILE,
            profile_id, started_at_s, self._svc.current_version()))
        return {"ok":receipt.accepted,"state":receipt.state,"profile_id":profile_id,
                "session_id":receipt.session_id,"session_revision":receipt.session_revision,
                "error":None if receipt.accepted else receipt.reason_code}

    def start_competition_runtime_sampling(self, forward_marker_lat: float, forward_marker_lon: float, *, started_at_s: float) -> dict[str, object]:
        operation_id=uuid.uuid4().hex
        receipt=self.calibration.start(CalibrationStart(operation_id, CalibrationMode.RUNTIME_FORWARD_MARKER,
            "competition_runtime", started_at_s, self._svc.current_version(), True,
            float(forward_marker_lat), float(forward_marker_lon)))
        return {"ok":receipt.accepted,"state":receipt.state,"session_id":receipt.session_id,
                "session_revision":receipt.session_revision,
                "error":None if receipt.accepted else receipt.reason_code}

    def start_rtk_heading_sampling(self, *, started_at_s: float) -> dict[str, object]:
        """Start GPS+yaw sampling using RTK dual-antenna heading."""
        operation_id = uuid.uuid4().hex
        receipt = self.calibration.start(CalibrationStart(
            operation_id, CalibrationMode.RUNTIME_RTK_HEADING,
            "competition_runtime", started_at_s, self._svc.current_version(), True))
        return {"ok": receipt.accepted, "state": receipt.state, "session_id": receipt.session_id,
                "session_revision": receipt.session_revision,
                "error": None if receipt.accepted else receipt.reason_code}

    def observe_runtime_profile_sampling(self, observation: GpsObservation) -> dict[str, object]:
        receipt = self.calibration.observe(observation)
        return {"ok": receipt.accepted, "state": receipt.state,
                "session_id": receipt.session_id, "session_revision": receipt.session_revision,
                "auto_finalized": receipt.state == "applied",
                "error": None if receipt.accepted else receipt.reason_code}

    def finalize_runtime_profile_binding(self, *, completed_at_s: float) -> dict[str, object]:
        operation_id=uuid.uuid4().hex
        receipt=self.calibration.commit(operation_id, completed_at_s)
        return {"ok":receipt.accepted,"state":receipt.state,"session_id":receipt.session_id,
                "session_revision":receipt.session_revision,
                "error":None if receipt.accepted else receipt.reason_code}

    def cancel_runtime_profile_sampling(self) -> dict[str, object]:
        receipt=self.calibration.cancel(uuid.uuid4().hex)
        return {"ok":receipt.accepted,"state":receipt.state,"session_id":receipt.session_id,
                "error":None if receipt.accepted else receipt.reason_code}

    def _load_profile(self, profile_id: str) -> tuple[FieldProfile | None, list[str]]:
        try:
            return self.profile_repository.load_profile(profile_id), []
        except Exception as exc:
            return None, [str(exc)]
