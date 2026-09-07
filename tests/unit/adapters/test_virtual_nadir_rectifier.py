from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from yolo_app.attitude_history import AttitudeMatch, quaternion_from_euler_zyx
from yolo_app.config import (
    CameraCalibrationConfig,
    VirtualNadirAttitudeConfig,
    VirtualNadirConfig,
    VirtualOutputConfig,
)
from yolo_app.virtual_nadir import VirtualNadirRectifier


def _config() -> VirtualNadirConfig:
    return VirtualNadirConfig(
        True,
        "lock_on_start",
        False,
        VirtualNadirAttitudeConfig("127.0.0.1", 5011, "sitl", 1500, 128, 75, 150, 0),
        CameraCalibrationConfig(
            640, 480, None, None, None, None, 90.0, 73.739795,
            (0.0, 0.0, 0.0, 0.0, 0.0),
            ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            True,
        ),
        VirtualOutputConfig(
            640, 480, None, None, None, None, 90.0, 73.739795, (0, 0, 0)
        ),
    )


def _match(
    roll: float = 0.0,
    pitch: float = 0.0,
    yaw: float = 0.0,
    *,
    session: str = "link-a",
) -> AttitudeMatch:
    return AttitudeMatch(
        True,
        "ok",
        ("publisher", session, "sitl"),
        roll,
        pitch,
        yaw,
        quaternion_from_euler_zyx(roll, pitch, yaw),
        1,
        2,
        5.0,
    )


def _grid() -> np.ndarray:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    for x in range(0, 640, 40):
        cv2.line(image, (x, 0), (x, 479), (80, 80, 80), 1)
    for y in range(0, 480, 40):
        cv2.line(image, (0, y), (639, y), (80, 80, 80), 1)
    cv2.drawMarker(image, (320, 240), (255, 255, 255), cv2.MARKER_CROSS, 25, 2)
    cv2.arrowedLine(image, (320, 240), (320, 100), (0, 255, 0), 3)
    cv2.putText(image, "L", (80, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
    cv2.putText(image, "R", (540, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    return image


def _project(homography: np.ndarray, point: tuple[float, float]) -> tuple[float, float]:
    value = homography @ np.array((point[0], point[1], 1.0))
    return float(value[0] / value[2]), float(value[1] / value[2])


def test_level_ideal_camera_is_identity() -> None:
    rectifier = VirtualNadirRectifier(_config())
    image = _grid()

    result = rectifier.rectify(image, _match())

    assert result.valid
    assert np.allclose(rectifier.last_homography, np.eye(3), atol=1e-9)
    assert np.array_equal(result.frame, image)
    assert np.all(result.valid_mask == 255)


@pytest.mark.parametrize("roll_deg,pitch_deg", [(15, 0), (-15, 0), (0, 15), (0, -15)])
def test_roll_and_pitch_generate_full_projective_correction(
    roll_deg: float, pitch_deg: float
) -> None:
    rectifier = VirtualNadirRectifier(_config())
    image = _grid()
    rectifier.rectify(image, _match())

    result = rectifier.rectify(
        image, _match(math.radians(roll_deg), math.radians(pitch_deg), 0.0)
    )

    assert result.valid
    assert not np.allclose(rectifier.last_homography, np.eye(3), atol=1e-6)
    assert 0 < np.count_nonzero(result.valid_mask) < result.valid_mask.size


def test_yaw_lock_rotates_opposite_sides_in_opposite_image_directions() -> None:
    image = _grid()
    positive = VirtualNadirRectifier(_config())
    positive.rectify(image, _match(yaw=0.0))
    positive.rectify(image, _match(yaw=math.radians(30.0)))
    positive_h = positive.last_homography
    assert positive_h is not None

    negative = VirtualNadirRectifier(_config())
    negative.rectify(image, _match(yaw=0.0))
    negative.rectify(image, _match(yaw=math.radians(-30.0)))
    negative_h = negative.last_homography
    assert negative_h is not None

    positive_point = _project(positive_h, (420.0, 240.0))
    negative_point = _project(negative_h, (420.0, 240.0))
    assert positive_point[1] > 240.0
    assert negative_point[1] < 240.0
    assert positive.yaw_ref_rad == pytest.approx(0.0)
    assert negative.yaw_ref_rad == pytest.approx(0.0)


def test_yaw_equal_to_reference_is_identity_and_mixed_attitude_is_finite() -> None:
    rectifier = VirtualNadirRectifier(_config())
    image = _grid()
    reference = math.radians(42.0)
    level = rectifier.rectify(image, _match(yaw=reference))
    assert level.valid and np.allclose(rectifier.last_homography, np.eye(3), atol=1e-9)

    mixed = rectifier.rectify(
        image,
        _match(math.radians(12), math.radians(-9), math.radians(75)),
    )
    assert mixed.valid
    assert rectifier.last_homography is not None
    assert np.isfinite(rectifier.last_homography).all()


def test_link_session_change_relocks_yaw_reference() -> None:
    rectifier = VirtualNadirRectifier(_config())
    image = _grid()
    rectifier.rectify(image, _match(yaw=0.3, session="link-a"))
    rectifier.rectify(image, _match(yaw=0.8, session="link-a"))
    assert rectifier.yaw_ref_rad == pytest.approx(0.3)

    result = rectifier.rectify(image, _match(yaw=-1.1, session="link-b"))
    assert result.valid
    assert rectifier.yaw_ref_rad == pytest.approx(-1.1)
    assert np.allclose(rectifier.last_homography, np.eye(3), atol=1e-9)


def test_manual_yaw_reference_reset_requires_current_valid_session() -> None:
    rectifier = VirtualNadirRectifier(_config())
    image = _grid()
    first = _match(yaw=0.3, session="link-a")
    rectifier.rectify(image, first)

    reset = _match(yaw=-0.8, session="link-a")
    assert rectifier.reset_yaw_reference(reset)
    assert rectifier.yaw_ref_rad == pytest.approx(-0.8)
    assert rectifier.last_homography is None

    other_session = _match(yaw=0.7, session="link-b")
    assert not rectifier.reset_yaw_reference(other_session)
    assert rectifier.yaw_ref_rad == pytest.approx(-0.8)


def test_invalid_attitude_does_not_reuse_previous_homography() -> None:
    rectifier = VirtualNadirRectifier(_config())
    image = _grid()
    rectifier.rectify(image, _match())

    result = rectifier.rectify(
        image,
        AttitudeMatch(False, "missing_attitude_after", ("publisher", "link-a", "sitl")),
    )

    assert not result.valid
    assert result.reason == "missing_attitude_after"
    assert np.count_nonzero(result.valid_mask) == 0
    assert np.count_nonzero(result.frame) > 0
    assert np.allclose(rectifier.last_homography, np.eye(3), atol=1e-9)


def test_distortion_map_path_and_extrinsic_validation() -> None:
    config = _config()
    calibrated = replace(
        config,
        camera=replace(
            config.camera,
            fx=320.0,
            fy=320.0,
            cx=320.0,
            cy=240.0,
            distortion=(0.02, -0.01, 0.001, -0.001, 0.0),
            approximate_calibration=False,
        ),
        output=replace(config.output, fx=320.0, fy=320.0, cx=320.0, cy=240.0),
    )
    rectifier = VirtualNadirRectifier(calibrated)
    result = rectifier.rectify(_grid(), _match())
    assert result.valid
    assert np.count_nonzero(result.valid_mask) > result.valid_mask.size * 0.9

    invalid = replace(
        config,
        camera=replace(
            config.camera,
            r_body_camera=((1.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0)),
        ),
    )
    with pytest.raises(ValueError, match="orthonormal"):
        VirtualNadirRectifier(invalid)
