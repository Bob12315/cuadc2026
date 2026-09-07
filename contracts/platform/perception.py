from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Mapping, Protocol, TypeAlias

from .common import ClockStamp, JsonValue, SchemaVersion


@dataclass(frozen=True, slots=True)
class Detection:
    track_id: int | None
    class_id: int
    class_name: str
    confidence: float
    x1_px: float
    y1_px: float
    x2_px: float
    y2_px: float


@dataclass(frozen=True, slots=True)
class PerceptionTarget:
    track_id: int
    class_name: str
    confidence: float
    center_x_px: float
    center_y_px: float


class RecordingState(str, Enum):
    IDLE = "IDLE"
    START_REQUESTED = "START_REQUESTED"
    RECORDING = "RECORDING"
    STOP_REQUESTED = "STOP_REQUESTED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PerceptionFrameSnapshot:
    schema: SchemaVersion
    producer_id: str
    yolo_process_session_id: str
    sequence: int
    frame_id: int
    captured_at: ClockStamp
    producer_clock_domain_id: str
    received_at_monotonic_ns: int
    image_width_px: int
    image_height_px: int
    target: PerceptionTarget | None
    detections: tuple[Detection, ...]
    truncated: bool
    original_detection_count: int
    producer_status: str
    recording_state: RecordingState = RecordingState.UNKNOWN
    recorder_boot_id: str | None = None
    recorder_session_id: str | None = None
    recording_path: str | None = None
    recording_frames: int = 0
    recording_error: str | None = None
    recording_expires_at_monotonic_ns: int | None = None


@dataclass(frozen=True, slots=True)
class PerceptionHealthSnapshot:
    healthy: bool
    active_session_id: str | None
    age_s: float | None
    reason_code: str
    revision: int


@dataclass(frozen=True, slots=True)
class SetTargetLock:
    track_id: int | None
    kind: Literal["set_target_lock"] = "set_target_lock"


@dataclass(frozen=True, slots=True)
class SetRecording:
    enabled: bool
    kind: Literal["set_recording"] = "set_recording"


@dataclass(frozen=True, slots=True)
class ResetVirtualNadir:
    """Set the virtual-nadir yaw reference to the next verified attitude sample."""

    kind: Literal["reset_virtual_nadir"] = "reset_virtual_nadir"


VisionCommand: TypeAlias = SetTargetLock | SetRecording | ResetVirtualNadir


@dataclass(frozen=True, slots=True)
class VisionCommandEnvelope:
    schema: SchemaVersion
    client_id: str
    client_session_id: str
    target_yolo_process_session_id: str
    command_id: str
    sequence: int
    ttl_ms: int
    sent_at: ClockStamp
    command: VisionCommand

    def __post_init__(self) -> None:
        if not all((self.client_id, self.client_session_id, self.target_yolo_process_session_id, self.command_id)):
            raise ValueError("vision command identity must not be empty")
        if self.sequence < 0 or self.ttl_ms <= 0:
            raise ValueError("invalid vision sequence or ttl")


class VisionResultState(str, Enum):
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class VisionSubmissionReceipt:
    receipt_id: str
    command_id: str
    result_state: VisionResultState
    reason_code: str
    replayed: bool = False

    def __post_init__(self) -> None:
        if not self.receipt_id or not self.command_id:
            raise ValueError("vision receipt identity must not be empty")


@dataclass(frozen=True, slots=True)
class VisionCommandStatus:
    command_id: str
    state: VisionResultState
    duplicate: bool
    reason_code: str
    locked_track_id: int | None
    recording_state: RecordingState
    recorder_session_id: str | None
    actual_path: str | None
    frames: int
    error: str | None
    detail: Mapping[str, JsonValue] | None = None
    receipt_id: str = ""
    replayed: bool = False
    recorder_boot_id: str | None = None


class PerceptionPort(Protocol):
    def snapshot(self) -> PerceptionFrameSnapshot | None: ...
    def wait_next(self, *, after_session_id: str, after_sequence: int,
                  timeout_s: float) -> PerceptionFrameSnapshot | None: ...
    def health(self) -> PerceptionHealthSnapshot: ...


class VisionCommandPort(Protocol):
    def submit(self, command: VisionCommandEnvelope) -> VisionSubmissionReceipt: ...
    def status(self, command_id: str) -> VisionCommandStatus: ...
