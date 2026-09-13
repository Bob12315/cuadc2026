from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2")

from yolo_app.raw_frame_recorder import RawFrameRecorder


def test_raw_frame_recorder_writes_mp4_frames(tmp_path) -> None:
    recorder = RawFrameRecorder(str(tmp_path), fps=5.0)
    frame = np.zeros((24, 32, 3), dtype=np.uint8)

    start_status = recorder.start(frame.shape)
    recorder.write(frame)
    stop_status = recorder.stop()

    assert start_status.recording is True
    assert start_status.path.endswith(".mp4")
    assert stop_status.recording is False
    assert stop_status.frames == 1
    assert (tmp_path / "camera").exists() is False
    assert (tmp_path / start_status.path.rsplit("/", 1)[-1]).exists()


def test_raw_frame_recorder_stops_after_five_minutes(tmp_path) -> None:
    now = [100.0]
    recorder = RawFrameRecorder(
        str(tmp_path),
        fps=5.0,
        monotonic=lambda: now[0],
    )
    frame = np.zeros((24, 32, 3), dtype=np.uint8)

    recorder.start(frame.shape)
    recorder.write(frame)
    now[0] += 299.9
    recorder.write(frame)
    assert recorder.recording is True
    assert recorder.frames == 2

    now[0] += 0.1
    recorder.write(frame)
    assert recorder.recording is False
    assert recorder.frames == 2


def test_repeated_start_refreshes_recording_timeout(tmp_path) -> None:
    now = [100.0]
    recorder = RawFrameRecorder(
        str(tmp_path),
        fps=5.0,
        monotonic=lambda: now[0],
    )
    frame = np.zeros((24, 32, 3), dtype=np.uint8)

    recorder.start(frame.shape)
    now[0] += 500.0
    recorder.start(frame.shape)
    now[0] += 299.0

    assert recorder.stop_if_expired() is False
    assert recorder.recording is True
    now[0] += 1.0
    assert recorder.stop_if_expired() is True
    assert recorder.recording is False
