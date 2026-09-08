from __future__ import annotations

import math

from contracts.effects import FlightCommand
from missions.common.actions.align_descend import AlignDescendAction


def _context(
    *,
    frame_id: int,
    detections: list[dict],
    altitude_m: float = 2.0,
) -> dict:
    return {
        "field_heading_yaw_rad": 0.0,
        "drone": {"relative_altitude": altitude_m},
        "scene": {"frame_id": frame_id, "detections": detections},
    }


def _detection(ex: float, ey: float, track_id: int = 1) -> dict:
    return {"track_id": track_id, "ex": ex, "ey": ey}


def _command(result) -> FlightCommand:
    assert len(result.effects) == 1
    command = result.effects[0]
    assert isinstance(command, FlightCommand)
    return command


def test_always_selects_the_target_nearest_the_image_centre_and_descends() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.0,
        "descend_speed_mps": 0.2,
        "kp_forward": 1.0,
        "kp_right": 1.0,
        "max_vx_mps": 0.5,
        "max_vy_mps": 0.5,
        "field_yaw_deg": 90.0,
    })

    result = action.update(_context(
        frame_id=1,
        detections=[_detection(0.4, 0.4, 11), _detection(0.1, -0.2, 12)],
    ))

    assert result.reason == "align_descending"
    assert result.detail["target_track_id"] == 12
    command = _command(result)
    assert command.params["vx_cmd"] == 0.2
    assert command.params["vy_cmd"] == 0.1
    assert command.params["vz_cmd"] == 0.2
    assert math.isclose(command.params["yaw_hold_rad"], math.pi / 2)


def test_descent_does_not_wait_for_alignment() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0, "descend_speed_mps": 0.2})

    result = action.update(_context(
        frame_id=1,
        detections=[_detection(0.9, 0.9)],
        altitude_m=2.0,
    ))

    assert result.reason == "align_descending"
    assert _command(result).params["vz_cmd"] == 0.2


def test_low_altitude_succeeds_when_three_of_five_frames_are_aligned() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.0,
        "release_deadband_ex": 0.1,
        "release_deadband_ey": 0.1,
    })

    samples = [(0.0, 0.0), (0.2, 0.0), (0.05, -0.05), (0.0, 0.2), (0.1, 0.1)]
    results = [
        action.update(_context(
            frame_id=index,
            detections=[_detection(ex, ey)],
            altitude_m=1.0,
        ))
        for index, (ex, ey) in enumerate(samples, start=1)
    ]

    assert all(not result.done for result in results[:4])
    assert results[-1].done and not results[-1].failed
    assert results[-1].reason == "alignment_confirmed"
    assert results[-1].detail["alignment_hits"] == 3
    command = _command(results[-1])
    assert command.params["vx_cmd"] == 0.0
    assert command.params["vy_cmd"] == 0.0
    assert command.params["vz_cmd"] == 0.0


def test_duplicate_frame_is_not_counted_twice() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0})

    for _ in range(5):
        result = action.update(_context(
            frame_id=7,
            detections=[_detection(0.0, 0.0)],
            altitude_m=1.0,
        ))

    assert not result.done
    assert result.detail["alignment_window"] == [True]


def test_missing_target_holds_and_counts_as_a_miss_at_low_altitude() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0})

    result = action.update(_context(frame_id=1, detections=[], altitude_m=1.0))

    assert not result.done and not result.failed
    assert result.reason == "target_not_found"
    assert result.detail["alignment_window"] == [False]
    command = _command(result)
    assert command.params["vx_cmd"] == 0.0
    assert command.params["vy_cmd"] == 0.0
    assert command.params["vz_cmd"] == 0.0


def test_timeout_after_thirty_seconds_fails_with_an_explicit_stop() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0})
    action.started_at -= 31.0

    result = action.update(_context(
        frame_id=1,
        detections=[_detection(0.0, 0.0)],
        altitude_m=1.0,
    ))

    assert result.failed and not result.done
    assert result.reason == "align_descend_timeout"
    command = _command(result)
    assert command.params["vx_cmd"] == 0.0
    assert command.params["vy_cmd"] == 0.0
    assert command.params["vz_cmd"] == 0.0


def test_drop_timeout_can_complete_with_an_explicit_stop() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0, "complete_on_timeout": True})
    action.started_at -= 31.0

    result = action.update(_context(
        frame_id=1,
        detections=[_detection(0.5, 0.5)],
        altitude_m=2.0,
    ))

    assert result.done and not result.failed
    assert result.reason == "alignment_timeout_accepted"
    command = _command(result)
    assert command.params["vx_cmd"] == 0.0
    assert command.params["vy_cmd"] == 0.0
    assert command.params["vz_cmd"] == 0.0


def test_disabled_alignment_completes_without_waiting() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0, "enabled": False})

    result = action.update(_context(frame_id=1, detections=[], altitude_m=2.5))

    assert result.done and not result.failed
    assert result.reason == "alignment_skipped"
    assert _command(result).params["vz_cmd"] == 0.0
