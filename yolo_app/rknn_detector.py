from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import cv2
import numpy as np


INPUT_SIZE = 640
CLASS_NAMES = ("Target", "bucket", "class_2")


@dataclass(slots=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float


class RknnDetector:
    """RK3588 RKNN YOLO detector for supported model-zoo and flat layouts."""

    def __init__(
        self,
        model_path: str,
        conf_thres: float,
        iou_thres: float,
        classes: list[int],
        class_names: tuple[str, ...] = CLASS_NAMES,
        npu_core: int | None = None,
    ) -> None:
        if Path(model_path).suffix.lower() != ".rknn":
            raise ValueError(f"RK3588 detector requires an .rknn model: {model_path}")
        from rknnlite.api import RKNNLite

        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.classes = set(classes)
        self.class_names = class_names
        self.rknn = RKNNLite(verbose=False)
        if self.rknn.load_rknn(model_path) != 0:
            raise RuntimeError(f"failed to load RKNN model: {model_path}")
        if npu_core is None:
            core_mask = RKNNLite.NPU_CORE_0_1_2
            core_label = "NPU_CORE_0_1_2"
        elif npu_core in {0, 1, 2}:
            core_mask = getattr(RKNNLite, f"NPU_CORE_{npu_core}")
            core_label = f"NPU_CORE_{npu_core}"
        else:
            self.rknn.release()
            raise ValueError("npu_core must be 0, 1, 2, or None")
        if self.rknn.init_runtime(core_mask=core_mask) != 0:
            self.rknn.release()
            raise RuntimeError(f"failed to initialize RKNN runtime on {core_label}")
        self.last_metrics_ms = {"preprocess": 0.0, "npu": 0.0, "postprocess": 0.0}

    def detect(self, frame) -> list[Detection]:
        started = time.perf_counter()
        data, scale, pad_x, pad_y = letterbox(frame)
        preprocessed = time.perf_counter()
        outputs = self.rknn.inference(inputs=[data])
        inferred = time.perf_counter()
        if outputs is None:
            raise KeyboardInterrupt
        detections = postprocess(
            outputs=outputs,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
            frame_shape=frame.shape,
            conf=self.conf_thres,
            iou=self.iou_thres,
            class_names=self.class_names,
        )
        postprocessed = time.perf_counter()
        self.last_metrics_ms = {
            "preprocess": (preprocessed - started) * 1000.0,
            "npu": (inferred - preprocessed) * 1000.0,
            "postprocess": (postprocessed - inferred) * 1000.0,
        }
        if not self.classes:
            return detections
        return [detection for detection in detections if detection.class_id in self.classes]

    def release(self) -> None:
        self.rknn.release()


def letterbox(image) -> tuple[np.ndarray, float, int, int]:
    height, width = image.shape[:2]
    scale = min(INPUT_SIZE / width, INPUT_SIZE / height)
    new_width, new_height = int(round(width * scale)), int(round(height * scale))
    pad_x = (INPUT_SIZE - new_width) // 2
    pad_y = (INPUT_SIZE - new_height) // 2
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
    canvas[pad_y : pad_y + new_height, pad_x : pad_x + new_width] = resized
    # RKNN input is RGB uint8 NHWC and must retain its batch dimension.
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)[None], scale, pad_x, pad_y


def _dfl(position: np.ndarray) -> np.ndarray:
    bins = position.shape[1] // 4
    logits = position.reshape(-1, 4, bins).astype(np.float32, copy=False)
    logits -= logits.max(axis=2, keepdims=True)
    probabilities = np.exp(logits)
    probabilities /= probabilities.sum(axis=2, keepdims=True)
    weights = np.arange(bins, dtype=np.float32).reshape(1, 1, bins)
    return (probabilities * weights).sum(axis=2)


def postprocess(
    outputs,
    scale: float,
    pad_x: int,
    pad_y: int,
    frame_shape,
    conf: float,
    iou: float,
    class_names: tuple[str, ...] = CLASS_NAMES,
) -> list[Detection]:
    if len(outputs) == 1:
        return _postprocess_flat_output(
            output=np.asarray(outputs[0]),
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
            frame_shape=frame_shape,
            conf=conf,
            iou=iou,
            class_names=class_names,
        )
    if len(outputs) != 9:
        raise ValueError(f"expected 1 or 9 RKNN YOLO outputs, got {len(outputs)}")

    all_boxes: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []
    for branch in range(3):
        position = np.asarray(outputs[branch * 3])
        class_scores = np.asarray(outputs[branch * 3 + 1])
        score_sum = np.asarray(outputs[branch * 3 + 2])[0, 0]
        rows, cols = np.where(score_sum >= conf)
        if rows.size == 0:
            continue

        distances = _dfl(position[0, :, rows, cols])
        grid = np.column_stack((cols, rows)).astype(np.float32) + 0.5
        stride = INPUT_SIZE // position.shape[2]
        boxes = np.column_stack((grid - distances[:, :2], grid + distances[:, 2:])) * stride
        scores = class_scores[0, :, rows, cols]
        all_boxes.append(boxes)
        all_scores.append(scores)

    if not all_boxes:
        return []

    boxes = np.concatenate(all_boxes)
    scores = np.concatenate(all_scores)
    class_ids = scores.argmax(axis=1)
    confidences = scores[np.arange(scores.shape[0]), class_ids]
    keep = confidences >= conf
    if not np.any(keep):
        return []

    return _build_detections(
        boxes=boxes[keep],
        confidences=confidences[keep],
        class_ids=class_ids[keep],
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
        frame_shape=frame_shape,
        conf=conf,
        iou=iou,
        class_names=class_names,
    )


def _postprocess_flat_output(
    output: np.ndarray,
    scale: float,
    pad_x: int,
    pad_y: int,
    frame_shape,
    conf: float,
    iou: float,
    class_names: tuple[str, ...],
) -> list[Detection]:
    if output.ndim == 4 and output.shape[2] == 1:
        output = np.squeeze(output, axis=2)
    if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] < 5:
        raise ValueError(f"expected flat RKNN YOLO output shape (1, 4 + classes, boxes), got {output.shape}")

    predictions = output[0].astype(np.float32, copy=False)
    scores = predictions[4:].T
    class_ids = scores.argmax(axis=1)
    confidences = scores[np.arange(scores.shape[0]), class_ids]
    keep = confidences >= conf
    if not np.any(keep):
        return []

    cx, cy, width, height = predictions[:4, keep]
    boxes = np.column_stack(
        (
            cx - width / 2.0,
            cy - height / 2.0,
            cx + width / 2.0,
            cy + height / 2.0,
        )
    )
    return _build_detections(
        boxes=boxes,
        confidences=confidences[keep],
        class_ids=class_ids[keep],
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
        frame_shape=frame_shape,
        conf=conf,
        iou=iou,
        class_names=class_names,
    )


def _build_detections(
    boxes: np.ndarray,
    confidences: np.ndarray,
    class_ids: np.ndarray,
    scale: float,
    pad_x: int,
    pad_y: int,
    frame_shape,
    conf: float,
    iou: float,
    class_names: tuple[str, ...],
) -> list[Detection]:
    boxes = boxes.astype(np.float32, copy=True)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / scale
    xywh = np.column_stack(
        (boxes[:, 0], boxes[:, 1], boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1])
    )
    # Suppress duplicate boxes only within their own class.  A single global
    # NMS pass drops a valid detection whenever two different classes overlap,
    # which makes multi-class output appear to be limited to a few labels.
    indices: list[int] = []
    for class_id in np.unique(class_ids):
        class_indices = np.flatnonzero(class_ids == class_id)
        selected = cv2.dnn.NMSBoxes(
            xywh[class_indices].astype(np.int32).tolist(),
            confidences[class_indices].tolist(),
            conf,
            iou,
        )
        if len(selected):
            indices.extend(class_indices[np.asarray(selected).reshape(-1)].tolist())
    if not indices:
        return []

    height, width = frame_shape[:2]
    detections = []
    for index in sorted(indices, key=lambda value: float(confidences[value]), reverse=True):
        x1, y1, x2, y2 = boxes[index]
        class_id = int(class_ids[index])
        class_name = class_names[class_id] if class_id < len(class_names) else str(class_id)
        detections.append(
            Detection(
                class_id=class_id,
                class_name=class_name,
                confidence=float(confidences[index]),
                x1=float(np.clip(x1, 0, width - 1)),
                y1=float(np.clip(y1, 0, height - 1)),
                x2=float(np.clip(x2, 0, width - 1)),
                y2=float(np.clip(y2, 0, height - 1)),
            )
        )
    return detections
