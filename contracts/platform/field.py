from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from .common import JsonValue, SchemaVersion


@dataclass(frozen=True, slots=True, order=True)
class ReferenceVersion:
    generation_id: str
    revision: int

    def __post_init__(self) -> None:
        if not self.generation_id or self.revision < 0:
            raise ValueError("invalid reference version")


@dataclass(frozen=True, slots=True)
class CalibrationSummary:
    session_id: str | None = None
    profile_id: str | None = None
    sample_count: int = 0
    rejected_sample_count: int = 0
    duplicate_sample_count: int = 0
    sample_duration_s: float | None = None
    horizontal_spread_m: float | None = None
    baseline_m: float | None = None
    field_reference_mode: str | None = None
    gps_fix_type: int | None = None
    gps_satellites: int | None = None
    gps_eph: float | None = None
    gps_epv: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldReferenceSnapshot:
    version: ReferenceVersion
    is_confirmed: bool
    is_frozen: bool
    origin_source: str | None
    heading_source: str | None
    origin_lat: float | None
    origin_lon: float | None
    forward_marker_lat: float | None
    forward_marker_lon: float | None
    field_heading_yaw_rad: float | None
    confirmed_at_s: float | None
    profile_id: str | None
    profile_name: str | None
    calibration: CalibrationSummary


@dataclass(frozen=True, slots=True)
class ReferenceWriteReceipt:
    accepted: bool
    operation_id: str
    previous_version: ReferenceVersion
    current_version: ReferenceVersion
    reason_code: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class FieldProfileRecord:
    schema: SchemaVersion
    profile_id: str
    name: str
    source: str
    source_priority: int
    content_sha256: str
    template_only: bool
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    content: Mapping[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class GpsObservation:
    observation_id: str
    observed_at_s: float
    global_position_valid: bool
    lat: float | None
    lon: float | None
    gps_fix_type: int
    satellites_visible: int
    gps_eph: float
    gps_epv: float
    # Source timestamp from GLOBAL_POSITION_INT.  This is distinct from the
    # local observation time and lets calibration reject stale GPS samples.
    last_global_position_time: float | None = None
    # Attitude fields for RTK heading mode
    attitude_valid: bool = False
    yaw: float | None = None


class CalibrationMode(str, Enum):
    REGISTERED_PROFILE = "registered_profile"
    RUNTIME_FORWARD_MARKER = "runtime_forward_marker"
    RUNTIME_RTK_HEADING = "runtime_rtk_heading"


@dataclass(frozen=True, slots=True)
class CalibrationStart:
    operation_id: str
    mode: CalibrationMode
    profile_id: str
    started_at_s: float
    base_version: ReferenceVersion
    auto_commit: bool = True
    forward_marker_lat: float | None = None
    forward_marker_lon: float | None = None


@dataclass(frozen=True, slots=True)
class CalibrationOperationReceipt:
    accepted: bool
    operation_id: str
    session_id: str | None
    session_revision: int
    state: str
    reason_code: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class CalibrationSessionSnapshot:
    session_id: str | None
    session_revision: int
    state: str
    base_version: ReferenceVersion | None
    profile_id: str | None
    candidate_ready: bool
    last_error: str | None


class FieldReferenceQueryPort(Protocol):
    def snapshot(self) -> FieldReferenceSnapshot: ...


class FieldReferenceVersionPort(Protocol):
    def current_version(self) -> ReferenceVersion: ...


class FieldProfileQueryPort(Protocol):
    def list(self) -> tuple[FieldProfileRecord, ...]: ...
    def get(self, profile_id: str) -> FieldProfileRecord: ...


class CalibrationTransactionPort(Protocol):
    def start(self, command: CalibrationStart) -> CalibrationOperationReceipt: ...
    def observe(self, observation: GpsObservation, *, expected_session_revision: int | None = None) -> CalibrationOperationReceipt: ...
    def preview(self) -> CalibrationSessionSnapshot: ...
    def commit(self, operation_id: str, completed_at_s: float, *, expected_session_revision: int | None = None) -> CalibrationOperationReceipt: ...
    def cancel(self, operation_id: str) -> CalibrationOperationReceipt: ...
    def status(self) -> CalibrationSessionSnapshot: ...
