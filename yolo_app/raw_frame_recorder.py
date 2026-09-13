from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import threading
import time
import uuid
from typing import Any

try:
    import cv2
except ImportError:  # Allows contract tests without installing desktop OpenCV.
    cv2 = None


@dataclass(slots=True)
class RawFrameRecorderStatus:
    recording: bool
    path: str
    frames: int
    error: str
    state: str
    recorder_boot_id: str
    recorder_session_id: str | None
    expires_at_monotonic: float | None


class RawFrameRecorder:
    DEFAULT_MAX_DURATION_S = 5.0 * 60.0

    def __init__(
        self,
        output_dir: str,
        fps: float,
        *,
        max_duration_s: float = DEFAULT_MAX_DURATION_S,
        monotonic: Any = time.monotonic,
        video_writer_factory: Any = None,
        fourcc_factory: Any = None,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.fps = fps if fps > 0 else 30.0
        self.max_duration_s = max(0.0, float(max_duration_s))
        self._monotonic = monotonic
        self._deadline_monotonic: float | None = None
        self._video_writer_factory = video_writer_factory or (
            None if cv2 is None else cv2.VideoWriter
        )
        self._fourcc_factory = fourcc_factory or (
            None if cv2 is None else cv2.VideoWriter_fourcc
        )
        self._lock = threading.RLock()
        self._expiry_timer: threading.Timer | None = None
        self.writer: Any = None
        self.path = ""
        self.frames = 0
        self.error = ""
        self.recorder_boot_id = uuid.uuid4().hex
        self.recorder_session_id: str | None = None
        self._state = "IDLE"

    @property
    def recording(self) -> bool:
        return self.writer is not None

    def handle_command(self, action: str, frame_shape: tuple[int, ...]) -> None:
        if action == "recording_start":
            self.start(frame_shape)
        elif action == "recording_stop":
            self.stop()

    def start(self, frame_shape: tuple[int, ...]) -> RawFrameRecorderStatus:
        with self._lock:
            if self.writer is not None:
                self._refresh_deadline()
                return self.status()
            self._state = "START_REQUESTED"
            height, width = int(frame_shape[0]), int(frame_shape[1])
            if self._video_writer_factory is None or self._fourcc_factory is None:
                self.error = "OpenCV VideoWriter is unavailable"
                self._state = "FAILED"
                return self.status()
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                path = self.output_dir / f"camera_{timestamp}.mp4"
                writer = self._video_writer_factory(
                    str(path), self._fourcc_factory(*"mp4v"), self.fps, (width, height),
                )
            except Exception as exc:
                self.error = f"failed to open recording writer: {exc}"
                self._state = "FAILED"
                return self.status()
            if not writer.isOpened():
                try:
                    writer.release()
                except Exception:
                    pass
                self.error = f"failed to open recording writer: {path}"
                self._state = "FAILED"
                return self.status()
            self.writer = writer
            self.path = str(path)
            self.frames = 0
            self.error = ""
            self.recorder_session_id = uuid.uuid4().hex
            self._state = "RECORDING"
            self._refresh_deadline()
            return self.status()

    def write(self, frame: Any) -> None:
        with self._lock:
            self.stop_if_expired()
            if self.writer is None:
                return
            try:
                self.writer.write(frame)
            except Exception as exc:
                self.error = f"recording write failed: {exc}"
                self._state = "FAILED"
                self._cancel_timer()
                self._deadline_monotonic = None
                self._release_writer()
                return
            self.frames += 1

    def stop(self) -> RawFrameRecorderStatus:
        with self._lock:
            self._state = "STOP_REQUESTED" if self.writer is not None else self._state
            self._cancel_timer()
            if not self._release_writer():
                self._state = "FAILED"
            self._deadline_monotonic = None
            if self._state != "FAILED":
                self._state = "STOPPED"
            return self.status()

    def stop_if_expired(self, *, now_monotonic: float | None = None) -> bool:
        with self._lock:
            if self.writer is None or self._deadline_monotonic is None:
                return False
            now = self._monotonic() if now_monotonic is None else float(now_monotonic)
            if now < self._deadline_monotonic:
                return False
            self.stop()
            return True

    def _refresh_deadline(self) -> None:
        self._deadline_monotonic = self._monotonic() + self.max_duration_s
        self._cancel_timer()
        self._expiry_timer = threading.Timer(self.max_duration_s, self._timer_expired)
        self._expiry_timer.daemon = True
        self._expiry_timer.start()

    def _timer_expired(self) -> None:
        with self._lock:
            self._expiry_timer = None
            if not self.stop_if_expired():
                remaining = (self._deadline_monotonic or self._monotonic()) - self._monotonic()
                if self.writer is not None and remaining > 0:
                    self._expiry_timer = threading.Timer(remaining, self._timer_expired)
                    self._expiry_timer.daemon = True
                    self._expiry_timer.start()

    def _cancel_timer(self) -> None:
        timer = self._expiry_timer
        self._expiry_timer = None
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()

    def _release_writer(self) -> bool:
        writer = self.writer
        self.writer = None
        if writer is None:
            return True
        try:
            writer.release()
            return True
        except Exception as exc:
            self.error = f"recording close failed: {exc}"
            return False

    def status(self) -> RawFrameRecorderStatus:
        with self._lock:
            return RawFrameRecorderStatus(
                recording=self.recording,
                path=self.path,
                frames=self.frames,
                error=self.error,
                state=self._state,
                recorder_boot_id=self.recorder_boot_id,
                recorder_session_id=self.recorder_session_id,
                expires_at_monotonic=self._deadline_monotonic,
            )

    def close(self) -> RawFrameRecorderStatus:
        return self.stop()
