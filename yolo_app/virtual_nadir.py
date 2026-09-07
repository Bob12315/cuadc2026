"""Pure-rotation Virtual Nadir Camera rectification.

Coordinate conventions
----------------------
NED world: X North, Y East, Z Down.
Body FRD: X Forward, Y Right, Z Down.
OpenCV camera: X image-right, Y image-down, Z optical-forward.

``R_body_camera`` is explicitly Camera -> Body: its columns are the real
camera axes expressed in Body FRD.  The ideal nadir camera maps camera X to
Body +Y, camera Y to Body -X, and camera Z to Body +Z.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import cv2
import numpy as np

try:
    from .attitude_history import AttitudeMatch
except ImportError:  # pragma: no cover - supports direct script execution
    from attitude_history import AttitudeMatch


R_BODY_CAMERA_IDEAL = np.array(
    ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)), dtype=np.float64
)


@dataclass(slots=True)
class RectificationResult:
    frame: np.ndarray
    valid_mask: np.ndarray
    valid: bool
    reason: str
    rectify_ms: float = 0.0
    attitude_match_ms: float | None = None
    roll_rad: float | None = None
    pitch_rad: float | None = None
    yaw_rad: float | None = None
    yaw_ref_rad: float | None = None
    attitude_rate_hz: float | None = None


class VirtualNadirRectifier:
    def __init__(self, config) -> None:
        self.config = config
        self.camera = config.camera
        self.output = config.output
        if self.camera.fx is None and not self.camera.approximate_calibration:
            raise ValueError(
                "FOV-derived camera intrinsics require approximate_calibration=true"
            )
        self.k_real = _camera_matrix(
            self.camera.width,
            self.camera.height,
            self.camera.fx,
            self.camera.fy,
            self.camera.cx,
            self.camera.cy,
            self.camera.fov_x_deg,
            self.camera.fov_y_deg,
            "camera",
        )
        self.k_virtual = _camera_matrix(
            self.output.width,
            self.output.height,
            self.output.fx,
            self.output.fy,
            self.output.cx,
            self.output.cy,
            self.output.fov_x_deg,
            self.output.fov_y_deg,
            "output",
        )
        self.k_real_inverse = np.linalg.inv(self.k_real)
        self.r_body_camera = np.asarray(
            self.camera.r_body_camera, dtype=np.float64
        ).reshape(3, 3)
        _validate_rotation(self.r_body_camera, "camera.r_body_camera")
        self.distortion = np.asarray(self.camera.distortion, dtype=np.float64)
        if self.distortion.size not in {4, 5, 8} or not np.isfinite(self.distortion).all():
            raise ValueError("camera.distortion must contain 4, 5, or 8 finite coefficients")
        self._undistort_map: tuple[np.ndarray, np.ndarray] | None = None
        if np.any(np.abs(self.distortion) > 1e-15):
            self._undistort_map = cv2.initUndistortRectifyMap(
                self.k_real,
                self.distortion,
                np.eye(3, dtype=np.float64),
                self.k_real,
                (self.camera.width, self.camera.height),
                cv2.CV_32FC1,
            )
        self._source_mask = np.full(
            (self.camera.height, self.camera.width), 255, dtype=np.uint8
        )
        self._session_key: tuple[str, str, str] | None = None
        self.yaw_ref_rad: float | None = None
        self.last_homography: np.ndarray | None = None

    def reset_yaw_reference(self, attitude: AttitudeMatch) -> bool:
        """Atomically establish a new reference from a verified frame attitude.

        The caller must discard all detections derived from the old reference
        before publishing another perception result.  A reference from another
        attitude session is deliberately rejected.
        """
        if (
            not attitude.valid
            or attitude.yaw_rad is None
            or attitude.session_key != self._session_key
            or not math.isfinite(attitude.yaw_rad)
        ):
            return False
        self.yaw_ref_rad = attitude.yaw_rad
        self.last_homography = None
        return True

    def rectify(self, frame: np.ndarray, attitude: AttitudeMatch) -> RectificationResult:
        started = time.perf_counter()
        if attitude.session_key != self._session_key:
            self._session_key = attitude.session_key
            self.yaw_ref_rad = None
            self.last_homography = None
        if not attitude.valid:
            return self._invalid(frame, attitude.reason, started, attitude)
        if frame.shape[:2] != (self.camera.height, self.camera.width):
            return self._invalid(frame, "camera_frame_size_mismatch", started, attitude)
        if self.config.yaw_reference_mode != "lock_on_start":
            return self._invalid(frame, "unsupported_yaw_reference_mode", started, attitude)
        if self.yaw_ref_rad is None:
            self.yaw_ref_rad = attitude.yaw_rad

        source = frame
        source_mask = self._source_mask
        if self._undistort_map is not None:
            map_x, map_y = self._undistort_map
            source = cv2.remap(
                frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
            )
            source_mask = cv2.remap(
                self._source_mask,
                map_x,
                map_y,
                cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )

        r_n_b_real = rotation_ned_from_body(
            attitude.roll_rad, attitude.pitch_rad, attitude.yaw_rad
        )
        r_n_c_real = r_n_b_real @ self.r_body_camera
        r_n_b_virtual = rotation_ned_from_body(0.0, 0.0, self.yaw_ref_rad)
        r_n_c_virtual = r_n_b_virtual @ R_BODY_CAMERA_IDEAL
        r_c_virtual_c_real = r_n_c_virtual.T @ r_n_c_real
        homography = self.k_virtual @ r_c_virtual_c_real @ self.k_real_inverse
        if not np.isfinite(homography).all() or abs(float(homography[2, 2])) < 1e-12:
            return self._invalid(frame, "invalid_homography", started, attitude)
        homography /= homography[2, 2]
        self.last_homography = homography.copy()
        size = self.output.width, self.output.height
        stabilized = cv2.warpPerspective(
            source,
            homography,
            size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=tuple(self.output.border_value),
        )
        valid_mask = cv2.warpPerspective(
            source_mask,
            homography,
            size,
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        return RectificationResult(
            stabilized,
            valid_mask,
            True,
            "ok",
            (time.perf_counter() - started) * 1000.0,
            attitude.attitude_match_ms,
            attitude.roll_rad,
            attitude.pitch_rad,
            attitude.yaw_rad,
            self.yaw_ref_rad,
            attitude.observed_rate_hz,
        )

    def _invalid(
        self,
        frame: np.ndarray,
        reason: str,
        started: float,
        attitude: AttitudeMatch,
    ) -> RectificationResult:
        size = self.output.width, self.output.height
        if frame.shape[:2] == (self.camera.height, self.camera.width):
            source = frame
            if self._undistort_map is not None:
                map_x, map_y = self._undistort_map
                source = cv2.remap(
                    frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
                )
            if self.last_homography is not None:
                invalid_frame = cv2.warpPerspective(
                    source,
                    self.last_homography,
                    size,
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=tuple(self.output.border_value),
                )
            else:
                invalid_frame = cv2.resize(source, size, interpolation=cv2.INTER_LINEAR)
        else:
            invalid_frame = cv2.resize(frame, size, interpolation=cv2.INTER_LINEAR)

        # This is display-only fail-soft output.  The all-zero valid mask keeps
        # inference disabled, while an explicit warning prevents a stale pose
        # preview from being mistaken for valid stabilized perception.
        cv2.rectangle(invalid_frame, (0, 0), (self.output.width, 58), (0, 0, 180), -1)
        cv2.putText(
            invalid_frame,
            "ATTITUDE SYNC LOST - YOLO PAUSED",
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            invalid_frame,
            reason,
            (10, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        return RectificationResult(
            invalid_frame,
            np.zeros((self.output.height, self.output.width), dtype=np.uint8),
            False,
            reason,
            (time.perf_counter() - started) * 1000.0,
            attitude.attitude_match_ms,
            attitude.roll_rad if attitude.valid else None,
            attitude.pitch_rad if attitude.valid else None,
            attitude.yaw_rad if attitude.valid else None,
            self.yaw_ref_rad,
            attitude.observed_rate_hz,
        )


def rotation_ned_from_body(roll_rad: float, pitch_rad: float, yaw_rad: float) -> np.ndarray:
    """Active Body-FRD -> NED rotation, Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    cr, sr = math.cos(roll_rad), math.sin(roll_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    rx = np.array(((1.0, 0.0, 0.0), (0.0, cr, -sr), (0.0, sr, cr)))
    ry = np.array(((cp, 0.0, sp), (0.0, 1.0, 0.0), (-sp, 0.0, cp)))
    rz = np.array(((cy, -sy, 0.0), (sy, cy, 0.0), (0.0, 0.0, 1.0)))
    return rz @ ry @ rx


def _camera_matrix(
    width: int,
    height: int,
    fx: float | None,
    fy: float | None,
    cx: float | None,
    cy: float | None,
    fov_x_deg: float | None,
    fov_y_deg: float | None,
    name: str,
) -> np.ndarray:
    explicit = (fx, fy, cx, cy)
    if all(value is not None for value in explicit):
        values = tuple(float(value) for value in explicit)
    elif any(value is not None for value in explicit):
        raise ValueError(f"{name} fx/fy/cx/cy must be all-present or all-absent")
    else:
        if fov_x_deg is None or fov_y_deg is None:
            raise ValueError(f"{name} requires K or both FOV values")
        fov_x = math.radians(float(fov_x_deg))
        fov_y = math.radians(float(fov_y_deg))
        if not 0.0 < fov_x < math.pi or not 0.0 < fov_y < math.pi:
            raise ValueError(f"{name} FOV must be in (0, 180) degrees")
        values = (
            width / (2.0 * math.tan(fov_x / 2.0)),
            height / (2.0 * math.tan(fov_y / 2.0)),
            width / 2.0,
            height / 2.0,
        )
    fx_value, fy_value, cx_value, cy_value = values
    if (
        width <= 0
        or height <= 0
        or fx_value <= 0.0
        or fy_value <= 0.0
        or not all(math.isfinite(value) for value in values)
    ):
        raise ValueError(f"{name} camera matrix is invalid")
    return np.array(
        ((fx_value, 0.0, cx_value), (0.0, fy_value, cy_value), (0.0, 0.0, 1.0)),
        dtype=np.float64,
    )


def _validate_rotation(rotation: np.ndarray, name: str) -> None:
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError(f"{name} must be a finite 3x3 matrix")
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6):
        raise ValueError(f"{name} must be orthonormal")
    if not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-6):
        raise ValueError(f"{name} must have determinant +1")


def build_debug_comparison(raw: np.ndarray, result: RectificationResult) -> np.ndarray:
    """Create a local/SITL-only RAW | VIRTUAL NADIR comparison image."""
    virtual = result.frame
    target_height = min(raw.shape[0], virtual.shape[0])
    raw_view = cv2.resize(raw, (round(raw.shape[1] * target_height / raw.shape[0]), target_height))
    virtual_view = cv2.resize(
        virtual, (round(virtual.shape[1] * target_height / virtual.shape[0]), target_height)
    )
    combined = np.hstack((raw_view, virtual_view))
    cv2.putText(combined, "RAW", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(
        combined,
        "VIRTUAL NADIR",
        (raw_view.shape[1] + 12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
    )
    values = (
        f"valid={result.valid} reason={result.reason} "
        f"roll={_degrees(result.roll_rad)} pitch={_degrees(result.pitch_rad)} "
        f"yaw={_degrees(result.yaw_rad)} yaw_ref={_degrees(result.yaw_ref_rad)} "
        f"rate_hz={_number(result.attitude_rate_hz)} "
        f"match_ms={result.attitude_match_ms} rectify_ms={result.rectify_ms:.2f}"
    )
    cv2.putText(
        combined, values, (12, target_height - 14), cv2.FONT_HERSHEY_SIMPLEX,
        0.45, (255, 255, 255), 1, cv2.LINE_AA
    )
    return combined


def _degrees(value: float | None) -> str:
    return "n/a" if value is None else f"{math.degrees(value):.1f}"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"
