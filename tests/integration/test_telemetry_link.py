from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymavlink import mavutil

from telemetry_link.mavlink_command_encoder import MavlinkCommandEncoder
from telemetry_link.config import DEFAULT_CONFIG_PATH, EndpointConfig, TelemetryConfig, _build_config, load_config_file
from telemetry_link.link_manager import LinkManager, SourceRuntime
from telemetry_link.mavlink_client import MavlinkClient
from telemetry_link.models import ActionCommand, ActionType, ControlCommand, ControlType, GimbalRateCommand
from telemetry_link.state_cache import StateCache
from telemetry_link.telemetry_receiver import TelemetryReceiver


from contracts.platform.vehicle_commands import BodyVelocity
from telemetry_link.command_broker import CommandBroker
from telemetry_link.mavlink_command_adapter import MavlinkCommandAdapter


def _attach_broker(runtime):
    context = runtime.command_context
    context.run_id = context.execution_lease_id = "test-run"
    context.send_enabled = True
    sent = []
    broker = CommandBroker(writer=sent.append, source=lambda: runtime.name,
        link_session=lambda: "session", authorization_generation=lambda: context.authorization_generation,
        send_generation=lambda: context.send_generation, send_enabled=lambda: context.send_enabled,
        monotonic_ns=time.monotonic_ns)
    runtime.command_broker = broker
    runtime.command_adapter = MavlinkCommandAdapter(broker, SimpleNamespace(notify=lambda: None),
        source=runtime.name, session_id=lambda: "session", context=context)
    return broker, sent


def _endpoint(name: str) -> EndpointConfig:
    return EndpointConfig(
        name=name,
        connection_type="tcp",
        serial_port="/dev/null",
        baudrate=115200,
        udp_mode="udpin",
        udp_host="127.0.0.1",
        udp_port=14550,
        tcp_host="127.0.0.1",
        tcp_port=5762,
        eth_mode="udpin",
        eth_host="0.0.0.0",
        eth_port=14550,
    )


def _config(**overrides) -> TelemetryConfig:
    data = dict(
        data_source="dual",
        active_source="sitl",
        sitl=_endpoint("sitl"),
        real=_endpoint("real"),


        heartbeat_timeout_sec=0.05,
        rx_timeout_sec=0.05,
        reconnect_interval_sec=0.01,
        receiver_idle_sleep_sec=0.01,
        sender_idle_sleep_sec=0.01,
        request_message_intervals=False,
        message_interval_hz={},
        gimbal_mount_mode=2,
        gimbal_yaw_min_deg=-180.0,
        gimbal_yaw_max_deg=180.0,
        gimbal_pitch_min_deg=-180.0,
        gimbal_pitch_max_deg=180.0,
        log_level="INFO",
    )
    data.update(overrides)
    return TelemetryConfig(**data)


def test_switch_active_source_cancels_motion_on_all_sources() -> None:
    manager = LinkManager(_config())
    pending = []
    for runtime in manager.runtimes.values():
        broker, sent = _attach_broker(runtime)
        receipt = runtime.command_adapter.submit_control(ControlCommand(ControlType.VELOCITY, vx=1, frame=8))
        pending.append((broker, receipt, sent))
    assert manager.switch_active_source("real") is True
    assert manager.get_active_source() == "real"
    for broker, receipt, sent in pending:
        assert broker.status(receipt.command_id).queue_state.value == "CANCELLED"
        assert broker.drain_one() is None
        assert not any(isinstance(value.payload, BodyVelocity) for value in sent)


def test_switch_active_source_updates_shared_attitude_publisher_session() -> None:
    manager = LinkManager(_config(attitude_udp_enabled=True))
    assert manager.attitude_publisher is not None
    old_session = manager.attitude_publisher.publisher_session_id

    assert manager.switch_active_source("real") is True

    assert manager.attitude_publisher.source == "real"
    assert manager.attitude_publisher.publisher_session_id != old_session
    assert all(
        runtime.attitude_publisher is manager.attitude_publisher
        for runtime in manager.runtimes.values()
    )


def test_virtual_nadir_attitude_interval_is_forced_to_at_least_30_hz() -> None:
    cfg = _config(data_source="sitl", active_source="sitl", attitude_udp_enabled=True,
                  attitude_udp_rate_hz=30, message_interval_hz={"ATTITUDE": 10})
    runtime = SourceRuntime("sitl", cfg.sitl, cfg, StateCache(.05, .05),
                            SimpleNamespace(), threading.Event(), threading.Event())
    sent = []
    runtime.encoder = SimpleNamespace(_send_action=sent.append)
    runtime._request_default_message_intervals()
    assert len(sent) == 1
    assert sent[0].action_type == ActionType.REQUEST_MESSAGE_INTERVAL
    assert sent[0].params == {"message_name": "ATTITUDE", "rate_hz": 30}


class _FailingClient:
    connection_string = "fake"
    is_sitl = True
    target_system = 0
    target_component = 0

    def __init__(self) -> None:
        self.connect_calls = 0
        self.closed = False

    def connect(self) -> None:
        self.connect_calls += 1

    def wait_heartbeat(self, timeout: float) -> None:
        raise TimeoutError("no heartbeat")

    def close(self) -> None:
        self.closed = True


def test_source_runtime_start_returns_while_connect_retries_in_background() -> None:
    client = _FailingClient()
    runtime = SourceRuntime(
        name="sitl",
        endpoint=_endpoint("sitl"),
        cfg=_config(data_source="sitl", active_source="sitl"),
        state_cache=StateCache(heartbeat_timeout_sec=0.05, rx_timeout_sec=0.05),
        client=client,
        stop_event=threading.Event(),
        worker_stop_event=threading.Event(),
    )

    started_at = time.monotonic()
    runtime.start(logging.getLogger("test"))
    elapsed = time.monotonic() - started_at

    try:
        assert elapsed < 0.05
        assert runtime.monitor_thread is not None
        assert runtime.monitor_thread.is_alive()
    finally:
        runtime.stop()


def test_source_runtime_stop_workers_cancels_pending_motion_and_actions() -> None:
    cfg = _config(data_source="sitl", active_source="sitl")
    runtime = SourceRuntime("sitl", cfg.sitl, cfg, StateCache(.05, .05),
                            _FailingClient(), threading.Event(), threading.Event())
    broker, sent = _attach_broker(runtime)
    control = runtime.command_adapter.submit_control(ControlCommand(ControlType.VELOCITY, vx=1, frame=8))
    action = runtime.command_adapter.submit_action(ActionCommand(ActionType.ARM))
    runtime._stop_workers(close_client=False)
    assert broker.status(control.command_id).queue_state.value == "CANCELLED"
    assert broker.status(action.command_id).queue_state.value == "CANCELLED"
    assert broker.drain_one() is None
    assert runtime.command_adapter is None and runtime.command_broker is None
    assert sent == []  # no previously transmitted motion required a stop


def test_stale_link_monitor_cancels_pending_commands_and_closes_client() -> None:
    class StopAfterFirstWait:
        def is_set(self): return False
        def wait(self, timeout): return True
        def set(self): pass
    cfg = _config(data_source="sitl", active_source="sitl")
    runtime = SourceRuntime("sitl", cfg.sitl, cfg, StateCache(.05, .05),
                            _FailingClient(), StopAfterFirstWait(), threading.Event())
    runtime.state_cache.mark_connected(target_system=1, target_component=1,
                                      transport="tcp", now=time.time()-1)
    broker, sent = _attach_broker(runtime)
    action = runtime.command_adapter.submit_action(ActionCommand(ActionType.ARM))
    control = runtime.command_adapter.submit_control(ControlCommand(ControlType.VELOCITY, vx=1, frame=8))
    runtime._monitor_loop(logging.getLogger("test"))
    assert runtime.client.closed
    assert broker.status(action.command_id).queue_state.value == "CANCELLED"
    assert broker.status(control.command_id).queue_state.value == "CANCELLED"
    assert broker.drain_one() is None and sent == []


def test_load_config_rejects_quoted_false_bool(tmp_path, monkeypatch) -> None:
    path = tmp_path / "telemetry.yaml"
    path.write_text(
        """
data_source: sitl
active_source: sitl
sitl:
  connection_type: tcp
real:
  connection_type: tcp


heartbeat_timeout_sec: 0.05
rx_timeout_sec: 0.05
reconnect_interval_sec: 0.01
receiver_idle_sleep_sec: 0.01
sender_idle_sleep_sec: 0.01
request_message_intervals: "false"
message_interval_hz: {}
log_level: INFO
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="request_message_intervals must be a YAML bool"):
        load_config_file(path)


def test_load_config_file_uses_tracked_real_root_profile() -> None:
    cfg = load_config_file(DEFAULT_CONFIG_PATH)

    assert cfg.data_source == "real"
    assert cfg.active_source == "real"
    assert cfg.real.connection_type == "eth"


def test_load_config_file_uses_explicit_sitl_profile() -> None:
    from scripts.config.render_profile import render
    root = Path(__file__).resolve().parents[2]
    rendered = render(root, root / "config/profiles/rk3588-sitl/profile.yaml", write=False)
    cfg = _build_config(rendered["telemetry"])

    assert cfg.data_source == "sitl"
    assert cfg.active_source == "sitl"
    assert cfg.sitl.connection_type == "udp"


def test_load_config_parses_eth_endpoint(tmp_path, monkeypatch) -> None:
    path = tmp_path / "telemetry.yaml"
    path.write_text(
        """
data_source: real
active_source: real
sitl:
  connection_type: tcp
real:
  connection_type: eth
  eth_mode: udpout
  eth_host: 192.168.144.10
  eth_port: 14550


heartbeat_timeout_sec: 0.05
rx_timeout_sec: 0.05
reconnect_interval_sec: 0.01
receiver_idle_sleep_sec: 0.01
sender_idle_sleep_sec: 0.01
request_message_intervals: false
message_interval_hz: {}
log_level: INFO
""".lstrip(),
        encoding="utf-8",
    )

    cfg = load_config_file(path)

    assert cfg.real.connection_type == "eth"
    assert cfg.real.eth_mode == "udpout"
    assert cfg.real.eth_host == "192.168.144.10"
    assert cfg.real.eth_port == 14550


def test_mavlink_client_uses_eth_udp_connection_string(monkeypatch) -> None:
    calls = []

    def fake_open(url: str, baud: int | None = None):
        calls.append((url, baud))
        return object()

    endpoint = _endpoint("real")
    endpoint.connection_type = "eth"
    endpoint.eth_mode = "udpin"
    endpoint.eth_host = "0.0.0.0"
    endpoint.eth_port = 14550
    monkeypatch.setattr("telemetry_link.mavlink_client.open_mavlink_connection", fake_open)

    client = MavlinkClient(endpoint)
    client.connect()

    assert client.connection_string == "udpin:0.0.0.0:14550"
    assert calls == [("udpin:0.0.0.0:14550", None)]


def test_mavlink_client_uses_eth_tcp_connection_string(monkeypatch) -> None:
    calls = []

    def fake_open(url: str, baud: int | None = None):
        calls.append((url, baud))
        return object()

    endpoint = _endpoint("real")
    endpoint.connection_type = "eth"
    endpoint.eth_mode = "tcp"
    endpoint.eth_host = "192.168.144.10"
    endpoint.eth_port = 5760
    monkeypatch.setattr("telemetry_link.mavlink_client.open_mavlink_connection", fake_open)

    client = MavlinkClient(endpoint)
    client.connect()

    assert client.connection_string == "tcp:192.168.144.10:5760"
    assert calls == [("tcp:192.168.144.10:5760", None)]


def test_load_config_rejects_invalid_bool_string(tmp_path, monkeypatch) -> None:
    path = tmp_path / "telemetry.yaml"
    path.write_text(
        """
data_source: sitl
active_source: sitl
sitl:
  connection_type: tcp
real:
  connection_type: tcp


heartbeat_timeout_sec: 0.05
rx_timeout_sec: 0.05
reconnect_interval_sec: 0.01
receiver_idle_sleep_sec: 0.01
sender_idle_sleep_sec: 0.01
request_message_intervals: maybe
message_interval_hz: {}
log_level: INFO
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="request_message_intervals must be a YAML bool"):
        load_config_file(path)


def test_local_position_ned_updates_raw_local_position_without_overwriting_relative_altitude() -> None:
    cache = StateCache(heartbeat_timeout_sec=1.0, rx_timeout_sec=1.0)
    now = time.time()
    cache.mark_connected(target_system=1, target_component=1, transport="tcp", now=now)
    receiver = TelemetryReceiver(
        client=None,
        state_cache=cache,
        cfg=_config(data_source="sitl", active_source="sitl", rx_timeout_sec=1.0),
        stop_event=threading.Event(),
    )
    global_message = SimpleNamespace(lat=34_0000000, lon=108_0000000, alt=420_000, relative_alt=4_200)
    message = SimpleNamespace(x=1.25, y=-2.5, z=-3.75, vx=0.1, vy=0.2, vz=0.3)

    receiver._handle_message("GLOBAL_POSITION_INT", global_message, now)
    receiver._handle_message("LOCAL_POSITION_NED", message, now)

    state = cache.get_latest_drone_state_validated(time.time())
    assert state.local_position_valid is True
    assert state.local_x == pytest.approx(1.25)
    assert state.local_y == pytest.approx(-2.5)
    assert state.local_z == pytest.approx(-3.75)
    assert state.relative_altitude == pytest.approx(4.2)


class _FakeMav:
    def __init__(self) -> None:
        self.command_long_calls = []
        self.local_position_calls = []
        self.events = []

    def command_long_send(self, *args) -> None:
        self.command_long_calls.append(args)
        self.events.append(("command_long", args))

    def set_position_target_local_ned_send(self, *args) -> None:
        self.local_position_calls.append(args)
        self.events.append(("local_position", args))


class _FakeMaster:
    target_system = 1
    target_component = 2

    def __init__(self) -> None:
        self.mav = _FakeMav()


class _RawMessageClient:
    def __init__(self) -> None:
        self.master = _FakeMaster()

    def send_raw_message(self, callback) -> None:
        callback(self.master)


def _encoder_with_fake_client() -> tuple[MavlinkCommandEncoder, _RawMessageClient]:
    client = _RawMessageClient()
    sender = MavlinkCommandEncoder(
        client=client,
        state_cache=StateCache(heartbeat_timeout_sec=1.0, rx_timeout_sec=1.0),
        cfg=_config(data_source="sitl", active_source="sitl"),
    )
    return sender, client


def test_command_encoder_set_servo_uses_mav_cmd_do_set_servo() -> None:
    sender, client = _encoder_with_fake_client()

    sender._send_action(
        ActionCommand(
            action_type=ActionType.SET_SERVO,
            params={"channel": 9, "pwm": 1900},
        )
    )

    call = client.master.mav.command_long_calls[-1]
    assert call[2] == mavutil.mavlink.MAV_CMD_DO_SET_SERVO
    assert call[4:11] == (9.0, 1900.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_command_encoder_set_relay_uses_mav_cmd_do_set_relay() -> None:
    sender, client = _encoder_with_fake_client()

    sender._send_action(
        ActionCommand(
            action_type=ActionType.SET_RELAY,
            params={"relay_id": 0, "state": True},
        )
    )

    call = client.master.mav.command_long_calls[-1]
    assert call[2] == mavutil.mavlink.MAV_CMD_DO_SET_RELAY
    assert call[4:11] == (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_command_encoder_velocity_ignores_yaw_axis() -> None:
    sender, client = _encoder_with_fake_client()

    sender._send_velocity(
        client.master,
        ControlCommand(
            command_type=ControlType.VELOCITY,
            vx=1.0,
            vy=2.0,
            vz=3.0,
            frame=8,
        ),
    )

    call = client.master.mav.local_position_calls[-1]
    type_mask = call[4]
    assert type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
    assert type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
    assert call[8:11] == (1.0, 2.0, 3.0)


def test_command_encoder_velocity_with_yaw_holds_yaw_axis() -> None:
    sender, client = _encoder_with_fake_client()

    sender._send_velocity(
        client.master,
        ControlCommand(
            command_type=ControlType.VELOCITY,
            vx=1.0,
            vy=2.0,
            vz=3.0,
            yaw=1.25,
            frame=8,
        ),
    )

    call = client.master.mav.local_position_calls[-1]
    type_mask = call[4]
    assert not type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
    assert type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
    assert call[8:11] == (1.0, 2.0, 3.0)
    assert call[14] == pytest.approx(1.25)


def test_command_encoder_body_velocity_with_zero_yaw_rate_uses_mask_1479() -> None:
    sender, client = _encoder_with_fake_client()
    sender._send_velocity(
        client.master,
        ControlCommand(
            command_type=ControlType.VELOCITY,
            vx=0.2,
            vy=-0.1,
            vz=0.15,
            yaw_rate=0.0,
            frame=8,
        ),
    )

    call = client.master.mav.local_position_calls[-1]
    type_mask = call[4]
    assert call[3] == 8
    assert type_mask == 1479
    for bit in (
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE,
    ):
        assert type_mask & bit
    for bit in (
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE,
        mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE,
    ):
        if bit == mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE:
            assert type_mask & bit
        else:
            assert not type_mask & bit
    assert call[8:11] == pytest.approx((0.2, -0.1, 0.15))
    assert call[14] == pytest.approx(0.0)
    assert call[15] == pytest.approx(0.0)


def test_command_encoder_rejects_velocity_with_yaw_and_yaw_rate() -> None:
    sender, client = _encoder_with_fake_client()
    with pytest.raises(ValueError, match="both yaw and yaw_rate"):
        sender._send_velocity(
            client.master,
            ControlCommand(command_type=ControlType.VELOCITY, yaw=1.0, yaw_rate=0.0, frame=8),
        )


def test_command_encoder_explicit_yaw_rate_keeps_yaw_rate_enabled() -> None:
    sender, client = _encoder_with_fake_client()

    sender._send_yaw_rate(
        client.master,
        ControlCommand(command_type=ControlType.YAW_RATE, yaw_rate=0.5, frame=8),
    )

    call = client.master.mav.local_position_calls[-1]
    type_mask = call[4]
    assert type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
    assert not type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
    assert call[-1] == pytest.approx(0.5)


def test_command_encoder_local_position_can_hold_yaw() -> None:
    sender, client = _encoder_with_fake_client()

    sender._send_local_position(
        client.master,
        ActionCommand(
            action_type=ActionType.LOCAL_POSITION,
            params={"x": 1.0, "y": 2.0, "z": -3.0, "frame": 1, "yaw": 0.75},
        ),
    )

    call = client.master.mav.local_position_calls[-1]
    type_mask = call[4]
    assert not type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
    assert type_mask & mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
    assert call[5:8] == (1.0, 2.0, -3.0)
    assert call[14] == pytest.approx(0.75)


# ── SITL 前安全修复：CommandQueue / MavlinkCommandEncoder 原子 stop-and-clear ────


