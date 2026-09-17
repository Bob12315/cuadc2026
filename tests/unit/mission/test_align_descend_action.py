from __future__ import annotations

import math

import pytest

from contracts.effects import ConditionYaw, FlightCommand
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
    commands = [effect for effect in result.effects if isinstance(effect, FlightCommand)]
    assert len(commands) == 1
    return commands[0]


def _yaw_lock(result) -> ConditionYaw:
    locks = [effect for effect in result.effects if isinstance(effect, ConditionYaw)]
    assert len(locks) == 1
    return locks[0]


def test_acquires_the_target_nearest_the_image_centre_and_descends() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.0,
        "descend_speed_mps": 0.2,
        "kp_forward": 1.0,
        "kp_right": 1.0,
        "final_kp_forward": 0.5,
        "final_kp_right": 0.5,
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
    assert result.detail["locked_target_track_id"] == 12
    assert result.detail["target_lock_state"] == "acquired"
    command = _command(result)
    assert command.params["vx_cmd"] == 0.2
    assert command.params["vy_cmd"] == 0.1
    assert command.params["vz_cmd"] == 0.2
    lock = _yaw_lock(result)
    assert lock.params == {
        "yaw_deg": pytest.approx(90.0),
        "yaw_speed_deg_s": 20.0,
        "direction": 0,
        "relative": False,
    }
    assert lock.once is True
    assert "yaw_hold_rad" not in command.params
    assert command.params["yaw_rate_rad_s"] == 0.0


def test_keeps_the_locked_target_until_it_disappears_then_relocks_nearest_position() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.0,
        "kp_forward": 1.0,
        "kp_right": 1.0,
        "max_vx_mps": 1.0,
        "max_vy_mps": 1.0,
    })

    acquired = action.update(_context(
        frame_id=1,
        detections=[_detection(0.05, 0.05, 11), _detection(0.2, 0.2, 12)],
    ))
    still_locked = action.update(_context(
        frame_id=2,
        detections=[_detection(0.6, -0.4, 11), _detection(0.01, 0.01, 12)],
    ))
    relocked = action.update(_context(
        frame_id=3,
        detections=[_detection(0.2, 0.2, 12), _detection(0.03, -0.02, 13)],
    ))

    assert acquired.detail["locked_target_track_id"] == 11
    assert still_locked.detail["target_track_id"] == 11
    assert still_locked.detail["target_lock_state"] == "locked"
    assert _command(still_locked).params["vx_cmd"] == pytest.approx(0.4)
    assert _command(still_locked).params["vy_cmd"] == pytest.approx(0.6)
    assert relocked.detail["target_track_id"] == 13
    assert relocked.detail["locked_target_track_id"] == 13
    assert relocked.detail["target_lock_state"] == "relocked_nearest_position"


def test_reacquires_the_same_image_target_when_its_track_id_changes() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0})

    action.update(_context(
        frame_id=1,
        detections=[_detection(0.10, 0.10, 11), _detection(0.30, 0.30, 12)],
    ))
    reacquired = action.update(_context(
        frame_id=2,
        detections=[_detection(0.12, 0.09, 101), _detection(0.01, 0.01, 102)],
    ))

    assert reacquired.detail["target_track_id"] == 101
    assert reacquired.detail["locked_target_track_id"] == 101
    assert reacquired.detail["target_lock_state"] == "relocked_nearest_position"


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


def test_release_offset_applies_only_after_reaching_release_altitude() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.2,
        "release_target_ex": 0.0,
        "release_target_ey": 0.2,
        "kp_forward": 1.0,
        "kp_right": 1.0,
        "max_vx_mps": 1.0,
        "max_vy_mps": 1.0,
    })

    descending = action.update(_context(
        frame_id=1, detections=[_detection(0.0, 0.1)], altitude_m=1.3,
    ))
    final_height = action.update(_context(
        frame_id=2, detections=[_detection(0.0, 0.1)], altitude_m=1.2,
    ))

    assert descending.detail["release_offset_active"] is False
    assert descending.detail["alignment_error_ey"] == pytest.approx(0.1)
    assert _command(descending).params["vx_cmd"] == pytest.approx(-0.1)
    assert final_height.detail["release_offset_active"] is True
    assert final_height.detail["release_target_ey"] == pytest.approx(0.2)
    assert final_height.detail["alignment_error_ey"] == pytest.approx(-0.1)
    assert _command(final_height).params["vx_cmd"] == pytest.approx(0.1)
    assert _command(final_height).params["vz_cmd"] == 0.0


def test_final_altitude_stays_latched_through_a_small_height_rebound() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.2, "descend_speed_mps": 0.2})

    reached = action.update(_context(
        frame_id=1, detections=[_detection(0.2, 0.2)], altitude_m=1.2,
    ))
    rebounded = action.update(_context(
        frame_id=2, detections=[_detection(0.2, 0.2)], altitude_m=1.3,
    ))

    assert reached.detail["final_altitude_latched"] is True
    assert rebounded.reason == "confirming_alignment"
    assert rebounded.detail["speed_control_phase"] == "final_altitude"
    assert rebounded.detail["release_offset_active"] is True
    assert _command(rebounded).params["vz_cmd"] == 0.0


def test_final_altitude_uses_the_finer_velocity_limits() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.2,
        "kp_forward": 1.0,
        "kp_right": 1.0,
        "final_kp_forward": 0.5,
        "final_kp_right": 0.5,
        "max_vx_mps": 0.15,
        "max_vy_mps": 0.15,
        "final_max_vx_mps": 0.08,
        "final_max_vy_mps": 0.08,
    })

    descending = action.update(_context(
        frame_id=1, detections=[_detection(0.1, -0.1)], altitude_m=1.3,
    ))
    final_height = action.update(_context(
        frame_id=2, detections=[_detection(0.1, -0.1)], altitude_m=1.2,
    ))

    assert _command(descending).params["vx_cmd"] == pytest.approx(0.1)
    assert _command(descending).params["vy_cmd"] == pytest.approx(0.1)
    assert descending.detail["speed_control_phase"] == "descending"
    assert _command(final_height).params["vx_cmd"] == pytest.approx(0.05)
    assert _command(final_height).params["vy_cmd"] == pytest.approx(0.05)
    assert final_height.detail["speed_control_phase"] == "final_altitude"
    assert final_height.detail["active_max_vx_mps"] == pytest.approx(0.08)
    assert final_height.detail["active_kp_forward"] == pytest.approx(0.5)


def test_bounded_integral_increases_persistent_close_range_correction(monkeypatch) -> None:
    action = AlignDescendAction()
    clock = iter((100.0, 100.0, 100.2, 100.2, 100.4, 100.4))
    monkeypatch.setattr("missions.common.actions.align_descend.time.monotonic", lambda: next(clock))
    action.start({
        "target_altitude_m": 1.2,
        "kp_forward": 0.6,
        "kp_right": 0.6,
        "ki_forward": 0.25,
        "ki_right": 0.25,
        "integral_limit": 0.2,
        "max_vx_mps": 1.0,
        "max_vy_mps": 1.0,
    })

    first = action.update(_context(frame_id=1, detections=[_detection(0.1, -0.1)], altitude_m=1.2))
    second = action.update(_context(frame_id=2, detections=[_detection(0.1, -0.1)], altitude_m=1.2))

    assert _command(first).params["vx_cmd"] == pytest.approx(0.065)
    assert _command(second).params["vx_cmd"] == pytest.approx(0.07)
    assert _command(second).params["vy_cmd"] == pytest.approx(0.07)
    assert second.detail["integral_forward"] == pytest.approx(-0.04)
    assert second.detail["integral_right"] == pytest.approx(0.04)

def test_low_altitude_succeeds_when_two_of_five_frames_are_aligned() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.0,
        "release_deadband_ex": 0.1,
        "release_deadband_ey": 0.1,
    })

    samples = [(0.0, 0.0), (0.2, 0.0), (0.05, -0.05), (0.0, 0.2), (0.2, 0.2)]
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
    assert results[-1].detail["alignment_hits"] == 2
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
    assert command.params["control_frame"] == "MAV_FRAME_BODY_NED"
    assert command.params["yaw_mode"] == "condition_yaw_absolute"
    assert "yaw_hold_rad" not in command.params
    assert command.params["yaw_rate_rad_s"] == 0.0


def test_yaw_is_latched_for_target_loss_even_if_context_heading_changes() -> None:
    action = AlignDescendAction()
    action.start({"field_yaw_deg": 15.0, "target_altitude_m": 1.0})

    first = action.update({
        "field_heading_yaw_rad": math.radians(70.0),
        "drone": {"relative_altitude": 2.0},
        "scene": {"frame_id": 1, "detections": [_detection(0.1, 0.1)]},
    })
    lost_target = action.update({
        "field_heading_yaw_rad": math.radians(160.0),
        "drone": {"relative_altitude": 2.0},
        "scene": {"frame_id": 2, "detections": []},
    })

    lock = _yaw_lock(first)
    assert lock.params["yaw_deg"] == pytest.approx(85.0)
    assert lock.params["relative"] is False
    assert lock.params["yaw_speed_deg_s"] == 20.0
    holding = _command(lost_target)
    assert "yaw_hold_rad" not in holding.params
    assert holding.params["vx_cmd"] == holding.params["vy_cmd"] == holding.params["vz_cmd"] == 0.0
    assert lost_target.detail["yaw_source"] == "field_centerline"
    assert lost_target.detail["yaw_latched"] is True
    assert _yaw_lock(lost_target).params["yaw_deg"] == pytest.approx(85.0)


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
