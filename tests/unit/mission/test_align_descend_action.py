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
    velocity_mps: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    return {
        "field_heading_yaw_rad": 0.0,
        "drone": {
            "relative_altitude": altitude_m,
            "vx": velocity_mps[0],
            "vy": velocity_mps[1],
            "vz": velocity_mps[2],
        },
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


@pytest.mark.parametrize("offset_ey", [0.3, -0.4])
def test_release_offset_applies_from_descent_through_final_altitude(offset_ey: float) -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.2,
        "release_target_ex": 0.2,
        "release_target_ey": offset_ey,
        "kp_forward": 1.0,
        "kp_right": 1.0,
        "final_kp_forward": 0.5,
        "final_kp_right": 0.5,
        "max_vx_mps": 1.0,
        "max_vy_mps": 1.0,
    })
    descending = action.update(_context(
        frame_id=1, detections=[_detection(0.1, 0.1)], altitude_m=3.0,
    ))
    final_height = action.update(_context(
        frame_id=2, detections=[_detection(0.1, 0.1)], altitude_m=1.2,
    ))
    for result in (descending, final_height):
        assert result.detail["release_offset_active"] is True
        assert result.detail["release_target_ex"] == pytest.approx(0.2)
        assert result.detail["release_target_ey"] == pytest.approx(offset_ey)
        assert result.detail["alignment_error_ex"] == pytest.approx(-0.1)
        assert result.detail["alignment_error_ey"] == pytest.approx(0.1 - offset_ey)
        assert not result.done
    assert descending.detail["speed_control_phase"] == "descending"
    assert _command(descending).params["vx_cmd"] == pytest.approx(offset_ey - 0.1)
    assert _command(descending).params["vy_cmd"] == pytest.approx(-0.1)
    assert _command(descending).params["vz_cmd"] == pytest.approx(0.2)
    assert final_height.detail["speed_control_phase"] == "final_altitude"
    assert _command(final_height).params["vx_cmd"] == pytest.approx((offset_ey - 0.1) * 0.5)
    assert _command(final_height).params["vy_cmd"] == pytest.approx(-0.05)
    assert _command(final_height).params["vz_cmd"] == 0.0


def test_matching_offset_during_descent_does_not_complete_early() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.2, "release_target_ey": 0.3})
    for frame_id in range(1, 7):
        result = action.update(_context(
            frame_id=frame_id, detections=[_detection(0.0, 0.3)], altitude_m=3.0,
        ))
        assert result.detail["within_release_deadband"] is True
        assert not result.done
        assert _command(result).params["vx_cmd"] == pytest.approx(0.0)
        assert _command(result).params["vy_cmd"] == pytest.approx(0.0)
        assert _command(result).params["vz_cmd"] == pytest.approx(0.2)

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


def test_final_altitude_holds_zero_velocity_for_the_configured_settle_time(monkeypatch) -> None:
    action = AlignDescendAction()
    clock = iter((100.0, 100.1, 100.2, 100.3, 100.4, 100.6, 100.7, 100.8, 100.9, 101.0))
    monkeypatch.setattr("missions.common.actions.align_descend.time.monotonic", lambda: next(clock))
    action.start({
        "target_altitude_m": 1.2,
        "final_settle_time_s": 0.5,
        "final_kp_forward": 1.0,
        "final_kp_right": 1.0,
    })

    just_reached = action.update(_context(
        frame_id=1, detections=[_detection(0.2, 0.2)], altitude_m=1.2,
    ))
    still_settling = action.update(_context(
        frame_id=2, detections=[_detection(0.2, 0.2)], altitude_m=1.2,
    ))
    resumed = action.update(_context(
        frame_id=3, detections=[_detection(0.2, 0.2)], altitude_m=1.2,
    ))

    for result in (just_reached, still_settling):
        assert result.reason == "final_altitude_settling"
        assert result.detail["final_settling"] is True
        assert _command(result).params["vx_cmd"] == 0.0
        assert _command(result).params["vy_cmd"] == 0.0
        assert _command(result).params["vz_cmd"] == 0.0
    assert resumed.reason == "confirming_alignment"
    assert resumed.detail["final_settling"] is False
    assert _command(resumed).params["vx_cmd"] == pytest.approx(-0.2)
    assert _command(resumed).params["vy_cmd"] == pytest.approx(0.2)
    assert _command(resumed).params["vz_cmd"] == 0.0


def test_final_altitude_uses_the_finer_velocity_limits() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.2,
        "release_deadband_ex": 0.04,
        "release_deadband_ey": 0.04,
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
        "release_deadband_ex": 0.04,
        "release_deadband_ey": 0.04,
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

def test_low_altitude_succeeds_when_three_of_six_frames_are_aligned() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.0,
        "release_deadband_ex": 0.1,
        "release_deadband_ey": 0.1,
    })

    samples = [(0.2, 0.2), (0.0, 0.0), (0.05, -0.05), (0.0, 0.2), (0.2, 0.2), (0.0, 0.0)]
    results = [
        action.update(_context(
            frame_id=index,
            detections=[_detection(ex, ey)],
            altitude_m=1.0,
        ))
        for index, (ex, ey) in enumerate(samples, start=1)
    ]

    assert all(not result.done for result in results[:5])
    assert results[-1].done and not results[-1].failed
    assert results[-1].reason == "alignment_confirmed"
    assert results[-1].detail["alignment_hits"] == 3
    command = _command(results[-1])
    assert command.params["vx_cmd"] == 0.0
    assert command.params["vy_cmd"] == 0.0
    assert command.params["vz_cmd"] == 0.0


def test_release_waits_for_measured_horizontal_speed_below_the_limit() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.0,
        "release_max_speed_mps": 0.1,
    })

    results = [
        action.update(_context(
            frame_id=index,
            detections=[_detection(0.0, 0.0)],
            altitude_m=1.0,
            velocity_mps=(0.1, 0.0, 0.0),
        ))
        for index in range(1, 7)
    ]
    released = action.update(_context(
        frame_id=7,
        detections=[_detection(0.0, 0.0)],
        altitude_m=1.0,
        velocity_mps=(0.099, 0.0, 0.0),
    ))

    assert not results[-1].done
    assert results[-1].reason == "waiting_for_release_speed"
    assert results[-1].detail["within_release_speed_limit"] is False
    assert results[-1].detail["measured_speed_mps"] == pytest.approx(0.1)
    assert released.done and released.reason == "alignment_confirmed"
    assert released.detail["within_release_speed_limit"] is True
    assert released.detail["measured_speed_mps"] == pytest.approx(0.099)


def test_duplicate_frame_is_not_counted_twice() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.0})

    missed = action.update(_context(
        frame_id=6, detections=[_detection(0.2, 0.2)], altitude_m=1.0,
    ))
    assert not missed.done

    for _ in range(5):
        result = action.update(_context(
            frame_id=7,
            detections=[_detection(0.0, 0.0)],
            altitude_m=1.0,
        ))

    assert not result.done
    assert result.detail["alignment_window"] == [False, True]


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


def test_landing_alignment_descends_after_target_loss_then_hands_off_to_land() -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 0.3,
        "descend_speed_mps": 0.2,
        "continue_descending_on_target_loss": True,
    })

    initial = action.update(_context(
        frame_id=1, detections=[], altitude_m=2.5,
    ))
    assert initial.reason == "target_not_found"
    assert _command(initial).params["vz_cmd"] == 0.0

    action.update(_context(
        frame_id=2, detections=[_detection(0.1, 0.1)], altitude_m=2.0,
    ))
    lost = action.update(_context(
        frame_id=3, detections=[], altitude_m=1.2,
    ))
    assert lost.reason == "target_lost_descending"
    assert not lost.done and not lost.failed
    assert lost.detail["target_acquired"] is True
    command = _command(lost)
    assert command.params["vx_cmd"] == command.params["vy_cmd"] == 0.0
    assert command.params["vz_cmd"] == pytest.approx(0.2)

    reached = action.update(_context(
        frame_id=4, detections=[], altitude_m=0.3,
    ))
    assert reached.done and not reached.failed
    assert reached.reason == "target_lost_at_final_altitude"
    command = _command(reached)
    assert command.params["vx_cmd"] == command.params["vy_cmd"] == command.params["vz_cmd"] == 0.0


def test_initial_target_acquisition_timeout_completes_with_an_explicit_stop() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 0.3, "target_acquisition_timeout_s": 2.0})
    action.started_at -= 2.01

    result = action.update(_context(frame_id=1, detections=[], altitude_m=2.5))

    assert result.done and not result.failed
    assert result.reason == "target_acquisition_timeout_accepted"
    assert result.detail["target_acquired"] is False
    assert result.detail["target_acquisition_remaining_s"] == 0.0
    command = _command(result)
    assert command.params["vx_cmd"] == command.params["vy_cmd"] == command.params["vz_cmd"] == 0.0


def test_initial_target_acquisition_timeout_is_disabled_after_first_target() -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 0.3, "target_acquisition_timeout_s": 2.0})
    action.started_at -= 2.01

    result = action.update(_context(
        frame_id=1,
        detections=[_detection(0.1, -0.1)],
        altitude_m=2.5,
    ))

    assert not result.done and not result.failed
    assert result.reason == "align_descending"
    assert result.detail["target_acquired"] is True
    assert result.detail["target_acquisition_remaining_s"] is None


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
    action.started_at -= 30.01

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
    action.started_at -= 30.01

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


@pytest.mark.parametrize("vz", [0.2, -0.2, None, float("nan")])
def test_release_ignores_vertical_velocity(vz: float | None) -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.2, "release_max_speed_mps": 0.08})
    for frame_id in range(1, 7):
        context = _context(
            frame_id=frame_id, detections=[_detection(0.0, 0.0)],
            altitude_m=1.2, velocity_mps=(0.03, 0.04, 0.0),
        )
        if vz is None:
            context["drone"].pop("vz")
        else:
            context["drone"]["vz"] = vz
        result = action.update(context)
        assert result.detail["measured_speed_mps"] == pytest.approx(0.05)
    assert result.done
    assert result.reason == "alignment_confirmed"
    assert _command(result).params["vz_cmd"] == 0.0


@pytest.mark.parametrize("component", ["vx", "vy"])
@pytest.mark.parametrize("value", [None, float("nan")])
def test_missing_or_invalid_horizontal_velocity_blocks_release(component: str, value: float | None) -> None:
    action = AlignDescendAction()
    action.start({"target_altitude_m": 1.2})
    for frame_id in range(1, 7):
        context = _context(
            frame_id=frame_id, detections=[_detection(0.0, 0.0)], altitude_m=1.2,
        )
        context["drone"][component] = value
        result = action.update(context)
    assert not result.done
    assert result.reason == "waiting_for_release_speed"
    assert result.detail["measured_speed_mps"] is None
    assert result.detail["within_release_speed_limit"] is False


@pytest.mark.parametrize("ex, speed", [(0.2, 0.0), (0.0, 0.08), (0.0, 0.1), (0.0, None)])
def test_failed_arrival_check_uses_original_settling_path(ex: float, speed: float | None) -> None:
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.2, "final_settle_time_s": 0.3,
        "release_deadband_ex": 0.04, "release_max_speed_mps": 0.08,
    })
    context = _context(
        frame_id=1, detections=[_detection(ex, 0.0)], altitude_m=1.2,
    )
    context["drone"]["vx"] = speed
    result = action.update(context)
    assert not result.done
    assert result.reason == "final_altitude_settling"
    assert result.detail["final_settling"] is True
    command = _command(result)
    assert command.params["vx_cmd"] == command.params["vy_cmd"] == command.params["vz_cmd"] == 0.0


@pytest.mark.parametrize("offset_ey", [0.3, 0.2])
def test_aligned_arrival_still_waits_and_requires_final_height_vote(monkeypatch, offset_ey: float) -> None:
    now = [100.0]
    monkeypatch.setattr("missions.common.actions.align_descend.time.monotonic", lambda: now[0])
    action = AlignDescendAction()
    action.start({
        "target_altitude_m": 1.2, "final_settle_time_s": 0.3,
        "release_target_ey": offset_ey, "release_deadband_ex": 0.04,
        "release_deadband_ey": 0.04, "release_max_speed_mps": 0.08,
    })
    arrived = action.update(_context(
        frame_id=1, detections=[_detection(0.0, offset_ey)],
        altitude_m=1.2, velocity_mps=(0.0, 0.0, 0.2),
    ))
    assert not arrived.done
    assert arrived.reason == "final_altitude_settling"
    assert _command(arrived).params["vx_cmd"] == 0.0
    assert _command(arrived).params["vy_cmd"] == 0.0
    assert _command(arrived).params["vz_cmd"] == 0.0
    now[0] += 0.31
    for frame_id in range(2, 8):
        result = action.update(_context(
            frame_id=frame_id, detections=[_detection(0.0, offset_ey)],
            altitude_m=1.2, velocity_mps=(0.0, 0.0, 0.2),
        ))
        assert result.done is (frame_id == 7)
    assert result.reason == "alignment_confirmed"


@pytest.mark.parametrize("complete_on_timeout", [False, True])
def test_timeout_boundary_is_thirty_seconds(monkeypatch, complete_on_timeout: bool) -> None:
    clock = [100.0]
    monkeypatch.setattr("missions.common.actions.align_descend.time.monotonic", lambda: clock[0])
    action = AlignDescendAction()
    action.start({"complete_on_timeout": complete_on_timeout})
    clock[0] = 129.99
    pending = action.update(_context(
        frame_id=1, detections=[_detection(0.5, 0.5)], altitude_m=2.0,
    ))
    assert not pending.done and not pending.failed
    clock[0] = 130.0
    expired = action.update(_context(
        frame_id=2, detections=[_detection(0.5, 0.5)], altitude_m=2.0,
    ))
    assert expired.done is complete_on_timeout
    assert expired.failed is (not complete_on_timeout)
    command = _command(expired)
    assert command.params["vx_cmd"] == command.params["vy_cmd"] == command.params["vz_cmd"] == 0.0
