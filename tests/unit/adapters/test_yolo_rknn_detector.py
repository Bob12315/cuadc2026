from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("cv2")

from yolo_app.rknn_detector import Detection, RknnDetector, letterbox, postprocess
from yolo_app.tracker_runner import TrackerRunner, _IoUTracker, _filter_detections_by_valid_mask


def _empty_outputs():
    outputs = []
    for size in (80, 40, 20):
        outputs.extend(
            [
                np.zeros((1, 64, size, size), dtype=np.float32),
                np.zeros((1, 3, size, size), dtype=np.float32),
                np.zeros((1, 1, size, size), dtype=np.float32),
            ]
        )
    return outputs


def test_letterbox_produces_rgb_batched_uint8_input() -> None:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[0, 0] = [10, 20, 30]

    data, scale, pad_x, pad_y = letterbox(image)

    assert data.shape == (1, 640, 640, 3)
    assert data.dtype == np.uint8
    assert data[0, pad_y, pad_x].tolist() == [30, 20, 10]
    assert (scale, pad_x, pad_y) == (1.0, 0, 80)


def test_postprocess_uses_score_sum_candidate_and_dfl_branch_output() -> None:
    outputs = _empty_outputs()
    outputs[2][0, 0, 10, 20] = 0.9
    outputs[1][0, 1, 10, 20] = 0.8

    detections = postprocess(outputs, 1.0, 0, 0, (640, 640, 3), 0.25, 0.45)

    assert len(detections) == 1
    detection = detections[0]
    assert detection.class_name == "bucket"
    assert np.isclose(detection.confidence, 0.8)
    assert (detection.x1, detection.y1, detection.x2, detection.y2) == (104.0, 24.0, 224.0, 144.0)


def test_postprocess_supports_flat_single_class_output() -> None:
    output = np.zeros((1, 5, 2), dtype=np.float32)
    output[0, :, 0] = [120.0, 140.0, 40.0, 60.0, 0.8]

    detections = postprocess(
        [output],
        1.0,
        0,
        0,
        (640, 640, 3),
        0.25,
        0.45,
        class_names=("bucket",),
    )

    assert len(detections) == 1
    detection = detections[0]
    assert detection.class_name == "bucket"
    assert np.isclose(detection.confidence, 0.8)
    assert (detection.x1, detection.y1, detection.x2, detection.y2) == (100.0, 110.0, 140.0, 170.0)


def test_postprocess_keeps_overlapping_detections_from_different_classes() -> None:
    output = np.zeros((1, 6, 2), dtype=np.float32)
    output[0, :4] = [[120.0, 120.0], [140.0, 140.0], [80.0, 80.0], [80.0, 80.0]]
    output[0, 4] = [0.9, 0.1]
    output[0, 5] = [0.1, 0.8]

    detections = postprocess(
        [output], 1.0, 0, 0, (640, 640, 3), 0.25, 0.45, class_names=("bucket", "H")
    )

    assert [item.class_name for item in detections] == ["bucket", "H"]
    assert [item.confidence for item in detections] == pytest.approx([0.9, 0.8])


def test_rknn_iou_tracker_keeps_visible_detection_id() -> None:
    tracker = _IoUTracker(max_lost_frames=5)
    first = Detection(0, "Target", 0.9, 10, 10, 50, 50)
    shifted = Detection(0, "Target", 0.85, 12, 12, 52, 52)

    first_tracks = tracker.update([first])
    second_tracks = tracker.update([shifted])

    assert first_tracks[0].track_id == second_tracks[0].track_id


def test_tracker_reset_retires_all_previous_track_ids() -> None:
    tracker = _IoUTracker(max_lost_frames=5)
    detection = Detection(0, "Target", 0.9, 10, 10, 50, 50)
    old_id = tracker.update([detection])[0].track_id

    tracker.reset()
    new_id = tracker.update([detection])[0].track_id

    assert tracker.states
    assert new_id != old_id


def test_valid_mask_rejects_boxes_touching_warp_border() -> None:
    mask = np.full((100, 100), 255, dtype=np.uint8)
    mask[:, :20] = 0
    invalid = Detection(0, "Target", 0.9, 10, 30, 40, 60)
    valid = Detection(0, "Target", 0.9, 30, 30, 60, 60)

    filtered = _filter_detections_by_valid_mask([invalid, valid], mask)

    assert filtered == [valid]


def test_detector_rejects_non_rknn_models_before_runtime_loading() -> None:
    with pytest.raises(ValueError, match=r"requires an \.rknn model"):
        RknnDetector("model.onnx", 0.25, 0.45, [])


def test_tracker_runner_runs_three_rknn_contexts_concurrently_on_distinct_cores() -> None:
    barrier = threading.Barrier(3)
    created = []

    class FakeDetector:
        def __init__(self, **kwargs) -> None:
            self.npu_core = kwargs["npu_core"]
            self.last_metrics_ms = {"preprocess": 1.0, "npu": 2.0, "postprocess": 3.0}
            self.released = False
            created.append(self)

        def detect(self, frame):
            barrier.wait(timeout=1.0)
            x = float(frame[0, 0, 0])
            return [Detection(0, "Target", 0.9, x, 0.0, x + 10.0, 10.0)]

        def release(self) -> None:
            self.released = True

    cfg = SimpleNamespace(
        inference_workers=3,
        model_path="model.rknn",
        conf_thres=0.25,
        iou_thres=0.45,
        classes=[],
        class_names=["Target"],
        max_lost_frames=5,
    )
    runner = TrackerRunner(cfg, detector_factory=FakeDetector)
    try:
        tickets = [
            runner.submit(np.full((16, 16, 3), value, dtype=np.uint8))
            for value in (1, 2, 3)
        ]
        tracks = [runner.complete(ticket) for ticket in tickets]
        assert [detector.npu_core for detector in created] == [0, 1, 2]
        assert [frame_tracks[0].x1 for frame_tracks in tracks] == [1.0, 2.0, 3.0]
    finally:
        runner.release()

    assert all(detector.released for detector in created)
