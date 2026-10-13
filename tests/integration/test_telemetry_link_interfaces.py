"""Public transport regression tests against the sole typed command broker."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
import yaml
from pymavlink import mavutil

from contracts.platform.vehicle_commands import (
    BodyVelocity, ChangeSpeed, GlobalPositionTarget, LocalPositionTarget,
    QueueState, SetServo, StopMotion, SubmissionState,
)
from telemetry_link.command_broker import CommandBroker
from telemetry_link.config import DEFAULT_CONFIG_PATH, _build_config
from telemetry_link.frames import BODY_NED, LOCAL_NED
from telemetry_link.link_manager import LinkManager
from telemetry_link.mavlink_command_adapter import MavlinkCommandAdapter
import telemetry_link.link_manager as link_manager_module


@pytest.fixture
def transport(monkeypatch):
    # Construct transport without sockets or device access; drain synchronously.
    monkeypatch.setattr(link_manager_module, "MavlinkClient", lambda _: SimpleNamespace())
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    data.update(data_source="sitl", active_source="sitl", attitude_udp_enabled=False)
    manager = LinkManager(_build_config(data))
    runtime = manager.runtimes["sitl"]
    context = runtime.command_context
    context.run_id = context.execution_lease_id = "test-run"
    context.send_enabled = True
    sent = []
    broker = CommandBroker(writer=sent.append, source=lambda: "sitl", link_session=lambda: "session",
        authorization_generation=lambda: context.authorization_generation,
        send_generation=lambda: context.send_generation, send_enabled=lambda: context.send_enabled,
        monotonic_ns=time.monotonic_ns)
    runtime.command_broker = broker
    runtime.command_adapter = MavlinkCommandAdapter(broker, SimpleNamespace(notify=lambda: None),
        source="sitl", session_id=lambda: "session", context=context)
    return manager, broker, sent


def _drain(broker, sent, receipt):
    assert receipt.submission_state is SubmissionState.ACCEPTED
    broker.drain_one()
    assert sent[-1].command_id == receipt.command_id
    return sent[-1]


@pytest.mark.parametrize("yaw", [None, 0.5])
def test_local_position_preserves_coordinates_priority_and_optional_yaw(transport, yaw) -> None:
    manager, broker, sent = transport
    cmd = _drain(broker, sent, manager.local_position(1, 2, -3, LOCAL_NED, yaw, priority=4))
    assert cmd.payload == LocalPositionTarget(1, 2, -3, yaw)
    assert cmd.priority == 4


def test_local_position_latest_target_supersedes_previous_target(transport) -> None:
    manager, broker, sent = transport
    first = manager.local_position(1, 2, -3, LOCAL_NED)
    last = manager.local_position(10, 20, -30, LOCAL_NED)
    assert broker.status(first.command_id).queue_state is QueueState.SUPERSEDED
    assert _drain(broker, sent, last).payload == LocalPositionTarget(10, 20, -30)
    assert broker.drain_one() is None


def test_global_waypoint_keeps_explicit_yaw(transport) -> None:
    manager, broker, sent = transport
    cmd = _drain(broker, sent, manager.global_goto(34, 108, 3, 6, yaw_rad=1.25))
    assert cmd.payload == GlobalPositionTarget(34, 108, 3, 1.25)


def test_speed_action_keeps_current_typed_writer_gap_explicit(transport) -> None:
    manager, broker, sent = transport
    _drain(broker, sent, manager.change_speed(1.0))
    assert sent[-1].payload == ChangeSpeed(1.0)
    manager.change_speed(2.0)
    receipt = manager.global_goto(34, 108, 3, 6)
    broker.drain_one()
    cmd = _drain(broker, sent, receipt)
    assert cmd.payload == GlobalPositionTarget(34, 108, 3)
    # The current DTO does not carry the legacy position speed-reapply metadata.
    # This cleanup must not silently change the documented speed-reapply gap.
    assert not hasattr(cmd.payload, "speed_overrides")
    assert [c.payload for c in sent if isinstance(c.payload, ChangeSpeed)] == [ChangeSpeed(1.0), ChangeSpeed(2.0)]


@pytest.mark.parametrize("yaw,yaw_rate", [(None, None), (1.25, None), (None, 0.0)])
def test_body_velocity_preserves_values_and_yaw_choice(transport, yaw, yaw_rate) -> None:
    manager, broker, sent = transport
    cmd = _drain(broker, sent, manager.send_velocity_command(.1, -.2, .3,
        frame=BODY_NED, yaw_rad=yaw, yaw_rate_rad_s=yaw_rate))
    assert cmd.payload == BodyVelocity(.1, -.2, .3, yaw_rate, yaw)


def test_body_velocity_latest_wins(transport) -> None:
    manager, broker, sent = transport
    first = manager.send_body_velocity(1, 2, 3)
    last = manager.send_body_velocity(4, 5, 6)
    assert broker.status(first.command_id).queue_state is QueueState.SUPERSEDED
    assert _drain(broker, sent, last).payload == BodyVelocity(4, 5, 6)
    assert broker.drain_one() is None


def test_raw_local_velocity_is_rejected_without_implicit_body_conversion(transport) -> None:
    manager, broker, sent = transport
    receipt = manager.send_velocity_command(1, 2, 3, frame=LOCAL_NED)
    assert receipt.submission_state is SubmissionState.REJECTED
    assert receipt.reason_code == "unsupported_control_frame"
    assert broker.drain_one() is None and sent == []


def test_body_stop_is_typed_zero_motion(transport) -> None:
    manager, broker, sent = transport
    _drain(broker, sent, manager.send_body_velocity(1, 2, 3))
    manager.stop_body_velocity()
    broker.drain_one()
    assert sent[-1].payload == StopMotion()


def test_stop_and_clear_cancels_pending_motion_and_writes_trusted_zero_barrier(transport) -> None:
    manager, broker, sent = transport
    moving = manager.send_body_velocity(1, 2, 3)
    receipt = manager.stop_body_velocity_and_clear()
    assert broker.status(moving.command_id).queue_state is QueueState.CANCELLED
    assert receipt.barrier_disposition.value == "TRANSMITTED"
    assert sent[-1].payload == StopMotion()
    manager.clear_continuous_commands()
    assert broker.drain_one() is None
    assert all(item.payload == StopMotion() for item in sent)


@pytest.mark.parametrize("priority", [3, 1])
def test_servo_submission_preserves_output_pwm_and_priority(transport, priority) -> None:
    manager, broker, sent = transport
    cmd = _drain(broker, sent, manager.set_servo(9, 1835, priority))
    assert cmd.payload == SetServo(9, 1835) and cmd.priority == priority


def test_servo_wrapper_uses_servo_output_not_rc_channel(transport) -> None:
    manager, broker, sent = transport
    manager.set_servo_output_pwm(8, 1800)
    broker.drain_one()
    assert sent[-1].payload == SetServo(8, 1800)
    assert "SERVO" in (LinkManager.set_servo_output_pwm.__doc__ or "")


def test_goto_local_ned_wrapper_preserves_yaw(transport) -> None:
    manager, broker, sent = transport
    manager.goto_local_ned(1, 2, -3, yaw_rad=.75)
    broker.drain_one()
    assert sent[-1].payload == LocalPositionTarget(1, 2, -3, .75)


def test_hold_current_position_requires_valid_position(transport) -> None:
    manager, broker, sent = transport
    assert manager.hold_current_local_position() is False
    assert sent == []
    cache = manager.get_state_cache()
    now = time.time()
    cache.mark_connected(target_system=1, target_component=1, transport="tcp", now=now)
    cache.update_drone_state(local_position_valid=True, attitude_valid=True,
        local_x=12, local_y=34, local_z=-5, yaw=1,
        last_local_position_time=now, last_attitude_time=now)
    assert manager.hold_current_local_position(priority=0) is True
    broker.drain_one()
    assert sent[-1].payload == LocalPositionTarget(12, 34, -5, 1)


def test_navigation_cancel_keeps_pending_payload_command(transport) -> None:
    manager, broker, sent = transport
    navigation = manager.local_position(1, 2, -3, LOCAL_NED)
    payload = manager.set_servo(8, 1800)
    manager.clear_pending_local_position_actions()
    assert broker.status(navigation.command_id).queue_state is QueueState.CANCELLED
    assert _drain(broker, sent, payload).payload == SetServo(8, 1800)


def test_default_send_gate_rejects_transport_submission(transport) -> None:
    manager, broker, sent = transport
    manager.update_write_context(run_id="test-run", send_enabled=False)
    receipt = manager.set_servo(8, 1800)
    assert receipt.submission_state is SubmissionState.REJECTED
    assert receipt.reason_code == "system_send_disabled"
    assert broker.drain_one() is None and sent == []


def test_frames_constants_match_protocol() -> None:
    assert LOCAL_NED == mavutil.mavlink.MAV_FRAME_LOCAL_NED
    assert BODY_NED == mavutil.mavlink.MAV_FRAME_BODY_NED


@pytest.mark.parametrize("hold", [False, True])
def test_runtime_navigation_cleanup_preserves_stop_clear_hold_order(hold) -> None:
    from application.action_runtime import ActionRuntimeService
    calls = []
    link = SimpleNamespace(
        stop_body_velocity_and_clear=lambda: calls.append("stop"),
        clear_pending_local_position_actions=lambda: calls.append("clear"),
        hold_current_local_position=lambda priority=0: calls.append("hold"),
    )
    ActionRuntimeService.clear_navigation_queue(link, hold_current=hold)
    assert calls == ["stop", "clear"] + (["hold"] if hold else [])
