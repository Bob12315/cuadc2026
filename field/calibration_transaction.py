from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from contracts.platform.field import (
    CalibrationMode, CalibrationOperationReceipt, CalibrationSessionSnapshot,
    CalibrationStart, GpsObservation,
)
from .calibration_session import CalibrationSession
from .models import validate_wgs84_lat_lon
from .profile import validate_field_profile
from .profile_service import ReadOnlyFieldProfileRepository


class FieldCalibrationTransactionAdapter:
    """Typed application-facing adapter over the atomic calibration owner."""

    def __init__(self, session: CalibrationSession, profiles: ReadOnlyFieldProfileRepository) -> None:
        self.session = session; self.profiles = profiles

    def start(self, command: CalibrationStart) -> CalibrationOperationReceipt:
        if command.base_version != self.session._svc.current_version():
            return self._receipt(False, command.operation_id, "stale_reference_version")
        try:
            profile = self.profiles.load_profile(command.profile_id)
            kwargs = {}
            if command.mode == CalibrationMode.RUNTIME_FORWARD_MARKER:
                if command.forward_marker_lat is None or command.forward_marker_lon is None:
                    return self._receipt(False, command.operation_id, "forward_marker_required")
                validate_wgs84_lat_lon(command.forward_marker_lat, command.forward_marker_lon, reject_pole=True)
                profile = deepcopy(profile)
                profile.forward_marker = replace(profile.forward_marker, lat=command.forward_marker_lat,
                                                 lon=command.forward_marker_lon)
                diagnostics = validate_field_profile(profile)
                if not diagnostics.ok: return self._receipt(False, command.operation_id, diagnostics.errors[0])
                kwargs = dict(template_profile_id=command.profile_id,
                              runtime_profile_id="competition_runtime_session",
                              input_source="application_typed",
                              forward_marker_lat=command.forward_marker_lat,
                              forward_marker_lon=command.forward_marker_lon)
            elif command.mode == CalibrationMode.RUNTIME_RTK_HEADING:
                kwargs = dict(template_profile_id=command.profile_id,
                              runtime_profile_id="competition_runtime_rtk_session",
                              input_source="application_typed",
                              heading_mode="rtk")
            elif profile.extra.get("template_only") is True:
                return self._receipt(False, command.operation_id, "template_only_profile")
            result = self.session.start(profile, started_at_s=command.started_at_s,
                operation_id=command.operation_id, auto_commit=command.auto_commit, **kwargs)
            return self._from_result(command.operation_id, result)
        except Exception as exc:
            return self._receipt(False, command.operation_id, str(exc))

    def observe(self, observation: GpsObservation, *, expected_session_revision: int | None = None) -> CalibrationOperationReceipt:
        result = self.session.observe({
            "global_position_valid": observation.global_position_valid, "lat": observation.lat,
            "lon": observation.lon, "gps_fix_type": observation.gps_fix_type,
            "satellites_visible": observation.satellites_visible, "gps_eph": observation.gps_eph,
            "gps_epv": observation.gps_epv,
            "last_global_position_time": observation.last_global_position_time,
            "attitude_valid": observation.attitude_valid,
            "yaw": observation.yaw,
        }, observed_at_s=observation.observed_at_s, observation_id=observation.observation_id,
           expected_session_revision=expected_session_revision)
        return self._from_result(observation.observation_id, result)

    def preview(self) -> CalibrationSessionSnapshot: return self.status()

    def commit(self, operation_id: str, completed_at_s: float, *,
               expected_session_revision: int | None = None) -> CalibrationOperationReceipt:
        return self._from_result(operation_id, self.session.finalize(completed_at_s=completed_at_s,
            operation_id=operation_id, expected_session_revision=expected_session_revision))

    def cancel(self, operation_id: str) -> CalibrationOperationReceipt:
        return self._from_result(operation_id, self.session.cancel(operation_id))

    def status(self) -> CalibrationSessionSnapshot:
        status=self.session.status()
        return CalibrationSessionSnapshot(self.session._session_id, self.session._session_revision,
            str(status.get("state","idle")), self.session._base_version,
            None if status.get("profile_id") is None else str(status["profile_id"]),
            bool(status.get("candidate_ready",False)),
            None if status.get("last_error") is None else str(status["last_error"]))

    def _from_result(self, operation_id: str, result: dict[str, object]) -> CalibrationOperationReceipt:
        session_id = result.get("session_id", self.session._session_id)
        return CalibrationOperationReceipt(bool(result.get("ok")), operation_id,
            None if session_id is None else str(session_id),
            int(result.get("session_revision", self.session._session_revision)), str(result.get("state", self.session.state)),
            str(result.get("error") or result.get("reason_code") or ("accepted" if result.get("ok") else "rejected")),
            bool(result.get("replayed",False)))

    def _receipt(self, accepted: bool, operation_id: str, reason: str) -> CalibrationOperationReceipt:
        return CalibrationOperationReceipt(accepted, operation_id, self.session._session_id,
            self.session._session_revision, self.session.state, reason)
