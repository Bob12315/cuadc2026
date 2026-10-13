from __future__ import annotations

import logging
import threading
import time
import uuid
from pymavlink import mavutil
from dataclasses import dataclass
from contracts.platform.vehicle_state import LinkControlSnapshot, SourceSwitchReceipt

try:
    from .attitude_udp_publisher import AttitudeUdpPublisher
    from .command_queue import CommandQueue
    from .command_sender import CommandSender
    from .config import EndpointConfig, TelemetryConfig
    from .frames import BODY_NED, LOCAL_NED
    from .mavlink_client import MavlinkClient
    from .models import ActionCommand, ActionType, ControlCommand, ControlType, DroneState, GimbalRateCommand, GimbalState, LinkStatus
    from .state_cache import StateCache
    from .telemetry_receiver import TelemetryReceiver
    from .command_shadow import LegacyOneShotShadow
    from .command_broker import CommandBroker, CommandBrokerWorker
    from .mavlink_command_adapter import MavlinkCommandAdapter, WriteContext
    from .mavlink_encoder import MavlinkEnvelopeWriter
    from .ack_router import AckRouter, AckSlot
    from contracts.platform.vehicle_commands import (BarrierDisposition, CancelRequest, CancelScope,
        CancellationReceipt, CommandSubmissionReceipt, SubmissionState, VehicleCommandEnvelope)
except ImportError:  # pragma: no cover - supports direct script execution
    from attitude_udp_publisher import AttitudeUdpPublisher
    from command_queue import CommandQueue
    from command_sender import CommandSender
    from config import EndpointConfig, TelemetryConfig
    from frames import BODY_NED, LOCAL_NED
    from mavlink_client import MavlinkClient
    from models import ActionCommand, ActionType, ControlCommand, ControlType, DroneState, GimbalRateCommand, GimbalState, LinkStatus
    from state_cache import StateCache
    from telemetry_receiver import TelemetryReceiver
    from command_shadow import LegacyOneShotShadow
    from command_broker import CommandBroker, CommandBrokerWorker
    from mavlink_command_adapter import MavlinkCommandAdapter, WriteContext
    from mavlink_encoder import MavlinkEnvelopeWriter
    from ack_router import AckRouter, AckSlot
    from contracts.platform.vehicle_commands import (BarrierDisposition, CancelRequest, CancelScope,
        CancellationReceipt, CommandSubmissionReceipt, SubmissionState, VehicleCommandEnvelope)


def _cancel_runtime(runtime, scope: CancelScope, **kwargs):
    broker = runtime.command_broker
    if broker is None:
        return None
    authorization_generation, send_generation, link_session_id = broker.cancellation_fence()
    return broker.cancel(CancelRequest.create(
        scope,
        expected_authorization_generation=authorization_generation,
        expected_send_generation=send_generation,
        expected_link_session_id=link_session_id,
        **kwargs,
    ))


@dataclass(slots=True)
class SourceRuntime:
    name: str
    endpoint: EndpointConfig
    cfg: TelemetryConfig
    state_cache: StateCache
    command_queue: CommandQueue
    client: MavlinkClient
    stop_event: threading.Event
    worker_stop_event: threading.Event
    receiver: TelemetryReceiver | None = None
    sender: CommandSender | None = None
    monitor_thread: threading.Thread | None = None
    worker_lock: threading.Lock | None = None
    command_context: WriteContext | None = None
    command_broker: CommandBroker | None = None
    broker_worker: CommandBrokerWorker | None = None
    command_adapter: MavlinkCommandAdapter | None = None
    ack_router: AckRouter | None = None
    legacy_sender_started: bool = False
    field_version_matches: object | None = None
    execution_fence_query: object | None = None
    attitude_publisher: AttitudeUdpPublisher | None = None

    def __post_init__(self) -> None:
        if self.worker_lock is None:
            self.worker_lock = threading.Lock()
        if self.command_context is None:
            self.command_context = WriteContext()

    def start(self, logger: logging.Logger) -> None:
        if self.monitor_thread is not None and self.monitor_thread.is_alive():
            return
        self.monitor_thread = threading.Thread(
            name=f"LinkMonitor-{self.name}",
            target=self._run_loop,
            args=(logger,),
            daemon=True,
        )
        self.monitor_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._stop_workers(close_client=True)
        if self.monitor_thread is not None:
            self.monitor_thread.join(timeout=1.0)

    def _connect_and_start_workers(self, logger: logging.Logger) -> None:
        while not self.stop_event.is_set():
            try:
                self.state_cache.mark_reconnecting()
                logger.info("source=%s Attempting to reconnect...", self.name)
                self.client.connect()
                self.client.wait_heartbeat(timeout=max(5.0, self.cfg.heartbeat_timeout_sec + 2.0))
                logger.info(
                    "source=%s link ready connection_type=%s endpoint=%s sitl_mode=%s target_system=%s target_component=%s",
                    self.name,
                    self.endpoint.connection_type,
                    self.client.connection_string,
                    self.client.is_sitl,
                    self.client.target_system,
                    self.client.target_component,
                )
                now = time.time()
                self.state_cache.mark_connected(
                    target_system=self.client.target_system,
                    target_component=self.client.autopilot_component,
                    transport=self.endpoint.connection_type,
                    now=now,
                )
                self.worker_stop_event = threading.Event()
                receiver_generation = self.state_cache.begin_receiver_generation()
                self.state_cache.mark_connected(
                    target_system=self.client.target_system,
                    target_component=self.client.autopilot_component,
                    transport=self.endpoint.connection_type,
                    now=time.time(),
                )
                bootstrap_state = getattr(self.client, "bootstrap_vehicle_state", lambda: None)()
                if isinstance(bootstrap_state, dict):
                    bootstrap_now = time.time()
                    self.state_cache.update_drone_state(
                        connected=True, stale=False, last_heartbeat_time=bootstrap_now,
                        **bootstrap_state,
                    )
                    self.state_cache.update_link(
                        connected=True, reconnecting=False, last_rx_time=bootstrap_now,
                        last_heartbeat_time=bootstrap_now, status_text="connected",
                    )
                if self.cfg.v2_writer_enabled:
                    self.sender = CommandSender(
                        self.client, self.command_queue, self.state_cache, self.cfg,
                        self.worker_stop_event, propagate_errors=True,
                    )
                    assert self.command_context is not None
                    writer = MavlinkEnvelopeWriter(
                        self.sender,
                        link_session_id=lambda: str(self.state_cache.atomic_publication(time.time())["session_id"]),
                        target_system=lambda: self.client.target_system,
                        target_component=lambda: self.client.autopilot_component,
                        local_system=lambda: self.client.local_system,
                        local_component=lambda: self.client.local_component,
                        source=lambda: self.name,
                    )
                    self.command_broker = CommandBroker(
                        writer=writer, source=lambda: self.name,
                        link_session=lambda: str(self.state_cache.atomic_publication(time.time())["session_id"]),
                        authorization_generation=lambda: self.command_context.authorization_generation,
                        send_generation=lambda: self.command_context.send_generation,
                        send_enabled=lambda: self.command_context.send_enabled,
                        connected=lambda: self.state_cache.get_link_status().connected,
                        monotonic_ns=time.monotonic_ns,
                        field_version_matches=self.field_version_matches if callable(self.field_version_matches) else None,
                        execution_fence_query=self.execution_fence_query,
                    )
                    self.ack_router = AckRouter(
                        self.command_broker.update_ack,
                        quarantine_ns=self.cfg.ack_quarantine_ms * 1_000_000,
                    )
                    writer.ack_router = self.ack_router
                    self.receiver = TelemetryReceiver(
                        self.client, self.state_cache, self.cfg, self.worker_stop_event,
                        receiver_generation, self.ack_router, self.name, self.attitude_publisher,
                    )
                    self.receiver.start()
                    self.broker_worker = CommandBrokerWorker(
                        self.command_broker, idle_s=self.cfg.sender_idle_sleep_sec
                    )
                    self.command_adapter = MavlinkCommandAdapter(
                        self.command_broker, self.broker_worker, source=self.name,
                        session_id=lambda: str(self.state_cache.atomic_publication(time.time())["session_id"]),
                        context=self.command_context,
                    )
                    if self.cfg.request_message_intervals:
                        self._request_default_message_intervals(direct=True)
                    elif self.cfg.attitude_udp_enabled:
                        self._request_message_interval(
                            "ATTITUDE", self.cfg.attitude_udp_rate_hz, direct=True
                        )
                    self.broker_worker.start()
                else:
                    self.sender = CommandSender(
                        self.client, self.command_queue, self.state_cache, self.cfg,
                        self.worker_stop_event,
                    )
                    self.sender.start()
                    self.legacy_sender_started = True
                    if self.cfg.request_message_intervals:
                        self._request_default_message_intervals(direct=False)
                    elif self.cfg.attitude_udp_enabled:
                        self._request_message_interval(
                            "ATTITUDE", self.cfg.attitude_udp_rate_hz, direct=False
                        )
                if self.receiver is None:
                    self.receiver = TelemetryReceiver(
                        self.client, self.state_cache, self.cfg, self.worker_stop_event,
                        receiver_generation, self.ack_router, self.name, self.attitude_publisher,
                    )
                    self.receiver.start()
                logger.info("source=%s Reconnected successfully", self.name)
                return
            except Exception as exc:
                logger.warning("source=%s reconnect failed: %s", self.name, exc)
                self.client.close()
                if self.stop_event.wait(self.cfg.reconnect_interval_sec):
                    return

    def _stop_workers(self, close_client: bool) -> None:
        with self.worker_lock:
            if self.command_broker is not None:
                _cancel_runtime(self,
                    CancelScope.SOURCE, source=self.name, emit_stop_barrier=True,
                    reason="link_shutdown")
            if self.broker_worker is not None:
                self.broker_worker.close(timeout_s=1.0)
                self.broker_worker = None
            self.worker_stop_event.set()
            if self.receiver is not None:
                self.receiver.join(timeout=1.0)
                self.receiver = None
            if self.sender is not None and self.legacy_sender_started:
                self.sender.join(timeout=1.0)
            self.legacy_sender_started = False
            self.sender = None
            self.command_adapter = None
            self.command_broker = None
            self.ack_router = None
            self.command_queue.clear_control()
            self.command_queue.clear_gimbal_rate()
            self.command_queue.clear_actions()
            if close_client:
                self.client.close()

    def _run_loop(self, logger: logging.Logger) -> None:
        self._connect_and_start_workers(logger)
        self._monitor_loop(logger)

    def _monitor_loop(self, logger: logging.Logger) -> None:
        while not self.stop_event.is_set():
            state = self.state_cache.get_latest_drone_state_validated(time.time())
            link = self.state_cache.get_link_status()
            if (not state.connected or state.stale) and not link.reconnecting:
                self.state_cache.mark_reconnecting()
                self._stop_workers(close_client=True)
                if self.stop_event.wait(self.cfg.reconnect_interval_sec):
                    break
                self._connect_and_start_workers(logger)
                continue
            if self.stop_event.wait(0.2):
                break

    def _request_default_message_intervals(self, *, direct: bool) -> None:
        requested_attitude = False
        for message_name, rate_hz in self.cfg.message_interval_hz.items():
            if self.stop_event.is_set():
                return
            if message_name == "ATTITUDE" and self.cfg.attitude_udp_enabled:
                rate_hz = max(rate_hz, self.cfg.attitude_udp_rate_hz)
                requested_attitude = True
            self._request_message_interval(message_name, rate_hz, direct=direct)
        if self.cfg.attitude_udp_enabled and not requested_attitude and not self.stop_event.is_set():
            self._request_message_interval(
                "ATTITUDE", self.cfg.attitude_udp_rate_hz, direct=direct
            )

    def _request_message_interval(
        self, message_name: str, rate_hz: float, *, direct: bool
    ) -> None:
        command = ActionCommand(
            action_type=ActionType.REQUEST_MESSAGE_INTERVAL,
            params={"message_name": message_name, "rate_hz": rate_hz},
            priority=20,
            retries_left=1,
            retry_interval_sec=self.cfg.action_retry_interval_sec,
            created_at=time.time(),
        )
        if direct and self.sender is not None:
            quarantine_id = uuid.uuid4().hex
            if self.ack_router is not None:
                publication = self.state_cache.atomic_publication(time.time())
                now_ns = time.monotonic_ns()
                self.ack_router.register(AckSlot(
                    quarantine_id, str(publication["session_id"]),
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    self.client.target_system, self.client.autopilot_component,
                    discard=True, ack_deadline_monotonic_ns=now_ns + 250_000_000,
                    total_deadline_monotonic_ns=now_ns + 250_000_000,
                    local_system=self.client.local_system,
                    local_component=self.client.local_component,
                ))
            self.sender._send_action(command)
            if self.ack_router is not None:
                self.ack_router.mark_transmitted(quarantine_id)
                deadline = time.monotonic_ns() + 250_000_000
                while self.ack_router.has_command(quarantine_id) and time.monotonic_ns() < deadline:
                    if self.stop_event.wait(0.005):
                        self.ack_router.abort(quarantine_id)
                        return
                self.ack_router.expire(time.monotonic_ns())
                # Every stream request shares MAV_CMD_SET_MESSAGE_INTERVAL's
                # ACK key. ACK/timeout closes the slot but starts quarantine;
                # do not register the next request until that protection ends.
                self.stop_event.wait(self.cfg.ack_quarantine_ms / 1000.0)
        else:
            self.command_queue.put_action(command)


class LinkManager:
    """
    Multi-source link manager.

    - Maintains separate runtimes for `real` and `sitl`.
    - Exposes only one active source outward.
    - Sends control commands only to the active source.
    """

    def __init__(self, cfg: TelemetryConfig) -> None:
        self.cfg = cfg
        self.logger = logging.getLogger(self.__class__.__name__)
        self.active_source = cfg.active_source
        self.active_lock = threading.Lock()
        self._source_revision = 0
        self._last_switch_cancel_receipt = None
        self._speed_override_lock = threading.Lock()
        self._speed_overrides: dict[str, dict[int, float]] = {}
        self._one_shot_shadows: dict[str, LegacyOneShotShadow] = {}
        self.runtimes: dict[str, SourceRuntime] = {}
        self._start_thread: threading.Thread | None = None
        self._field_version_port: object | None = None
        self._execution_fence_query: object | None = None
        self._attitude_stop_event = threading.Event()
        self.attitude_publisher = (
            AttitudeUdpPublisher(
                cfg.attitude_udp_ip,
                cfg.attitude_udp_port,
                source=cfg.active_source,
                stop_event=self._attitude_stop_event,
            )
            if cfg.attitude_udp_enabled
            else None
        )

        enabled_sources = (
            ["real", "sitl"] if cfg.data_source == "dual"
            else [cfg.data_source]
        )
        for source_name in enabled_sources:
            endpoint = cfg.real if source_name == "real" else cfg.sitl
            self.runtimes[source_name] = SourceRuntime(
                name=source_name,
                endpoint=endpoint,
                cfg=cfg,
                state_cache=StateCache(cfg.heartbeat_timeout_sec, cfg.rx_timeout_sec),
                command_queue=CommandQueue(),
                client=MavlinkClient(endpoint),
                stop_event=threading.Event(),
                worker_stop_event=threading.Event(),
                field_version_matches=self._field_version_matches,
                execution_fence_query=self._execution_fence_query,
                attitude_publisher=self.attitude_publisher,
            )
            self._speed_overrides[source_name] = {}
            runtime = self.runtimes[source_name]
            self._one_shot_shadows[source_name] = LegacyOneShotShadow(
                source=source_name,
                session_id=lambda runtime=runtime: str(runtime.state_cache.atomic_publication(time.time())["session_id"]),
            )

    def set_field_reference_version_port(self, port: object) -> None:
        self._field_version_port = port

    def set_execution_fence_query(self, port: object) -> None:
        """Composition-time binding; call before source workers start."""
        self._execution_fence_query = port
        for runtime in self.runtimes.values():
            if runtime.command_broker is not None:
                raise RuntimeError("execution fence cannot be rebound after broker start")
            runtime.execution_fence_query = port

    def _field_version_matches(self, version: object) -> bool:
        port = self._field_version_port
        checker = getattr(port, "version_matches", None)
        if not callable(checker):
            raise RuntimeError("field_version_checker_unavailable")
        return bool(checker(version))

    def cancel_stale_field_commands(self, reason: str = "field_reference_changed") -> None:
        for runtime in self.runtimes.values():
            broker = runtime.command_broker
            if broker is not None:
                _cancel_runtime(runtime, CancelScope.CONTINUOUS_STREAM,
                    stream_id="navigation", reason=reason)

    def start(self) -> None:
        if self.attitude_publisher is not None:
            self.attitude_publisher.start()
        for runtime in self.runtimes.values():
            runtime.start(self.logger)

    def start_background(self) -> threading.Thread:
        if self._start_thread is not None and self._start_thread.is_alive():
            return self._start_thread
        self._start_thread = threading.Thread(
            name="LinkManagerStart",
            target=self.start,
            daemon=True,
        )
        self._start_thread.start()
        return self._start_thread

    def stop(self) -> None:
        for runtime in self.runtimes.values():
            runtime.stop()
        self._attitude_stop_event.set()
        if self.attitude_publisher is not None:
            self.attitude_publisher.close()
        if self._start_thread is not None and self._start_thread.is_alive():
            self._start_thread.join(timeout=1.0)

    def get_active_source(self) -> str:
        with self.active_lock:
            return self.active_source

    def switch_active_source(self, source_name: str) -> bool:
        if source_name not in self.runtimes:
            self.logger.warning("switch_source failed: source=%s is not enabled by data_source=%s", source_name, self.cfg.data_source)
            return False
        previous_source = self.get_active_source()
        previous_runtime = self.runtimes[previous_source]
        barrier_receipt = None
        if previous_runtime.command_broker is not None:
            barrier_receipt = _cancel_runtime(previous_runtime,
                CancelScope.SOURCE, source=previous_source, emit_stop_barrier=True,
                reason="source_switch")
        for runtime in self.runtimes.values():
            if runtime.command_broker is not None:
                _cancel_runtime(runtime,
                    CancelScope.SOURCE, source=runtime.name, reason="source_switch_queue_clear",
                )
            runtime.command_queue.clear_actions()
        self._last_switch_cancel_receipt = barrier_receipt
        with self.active_lock:
            self.active_source = source_name
            self._source_revision += 1
        attitude_publisher = getattr(self, "attitude_publisher", None)
        if attitude_publisher is not None:
            attitude_publisher.switch_source(source_name)
        self._clear_continuous_commands()
        self.logger.info("switched active_source=%s previous_source=%s", source_name, previous_source)
        return True

    def get_link_control_snapshot(self) -> LinkControlSnapshot:
        with self.active_lock:
            source = self.active_source
            revision = self._source_revision
        runtime = self.runtimes.get(source)
        if runtime is None:
            return LinkControlSnapshot(source, revision, False, "unavailable")
        publication = runtime.state_cache.atomic_publication(time.time())
        return LinkControlSnapshot(source, revision, bool(publication["drone"].connected), str(publication["session_id"]))

    def activate_source(self, source_name: str, expected_revision: int) -> SourceSwitchReceipt:
        with self.active_lock:
            previous = self.active_source
            revision = self._source_revision
        if expected_revision != revision:
            return SourceSwitchReceipt(False, previous, previous, revision, "source_revision_conflict")
        accepted = self.switch_active_source(source_name)
        with self.active_lock:
            current = self.active_source
            revision = self._source_revision
        return SourceSwitchReceipt(accepted, previous, current, revision,
            "source_activated" if accepted else "source_not_enabled",
            None if self._last_switch_cancel_receipt is None else
                self._last_switch_cancel_receipt.barrier_disposition.value,
            None if self._last_switch_cancel_receipt is None else
                self._last_switch_cancel_receipt.barrier_id)

    def _clear_continuous_commands(self) -> None:
        for runtime in self.runtimes.values():
            runtime.command_queue.clear_control()
            runtime.command_queue.clear_gimbal_rate()

    def _active_runtime(self) -> SourceRuntime:
        source_name = self.get_active_source()
        return self.runtimes[source_name]

    def get_latest_drone_state(self) -> DroneState:
        runtime = self._active_runtime()
        return runtime.state_cache.get_latest_drone_state_validated(time.time())

    def get_latest_gimbal_state(self) -> GimbalState:
        runtime = self._active_runtime()
        return runtime.state_cache.get_latest_gimbal_state_validated(time.time())

    def get_latest_state(self) -> DroneState:
        return self.get_latest_drone_state()

    def get_latest_state_raw(self) -> DroneState:
        return self._active_runtime().state_cache.get_latest_drone_state_raw()

    def get_link_status(self) -> LinkStatus:
        return self._active_runtime().state_cache.get_link_status()

    def get_state_cache(self, source_name: str | None = None) -> StateCache:
        return self.runtimes[source_name or self.get_active_source()].state_cache

    def get_source_state(self, source_name: str) -> DroneState:
        runtime = self.runtimes[source_name]
        return runtime.state_cache.get_latest_drone_state_validated(time.time())

    def get_source_gimbal_state(self, source_name: str) -> GimbalState:
        runtime = self.runtimes[source_name]
        return runtime.state_cache.get_latest_gimbal_state_validated(time.time())

    def get_source_link_status(self, source_name: str) -> LinkStatus:
        return self.runtimes[source_name].state_cache.get_link_status()

    def is_connected(self) -> bool:
        link = self.get_link_status()
        now = time.time()
        heartbeat_expired = link.last_heartbeat_time > 0 and (now - link.last_heartbeat_time) > link.heartbeat_timeout_sec
        rx_expired = link.last_rx_time > 0 and (now - link.last_rx_time) > link.rx_timeout_sec
        return link.connected and not link.reconnecting and not heartbeat_expired and not rx_expired

    def submit_control_command(self, command: ControlCommand) -> None:
        runtime = self._active_runtime()
        if runtime.command_adapter is not None:
            return runtime.command_adapter.submit_control(command)
        if self.cfg.v2_writer_enabled:
            return CommandSubmissionReceipt("unavailable", SubmissionState.REJECTED, "command_backend_unavailable", uuid.uuid4().hex)
        runtime.command_queue.put_control(command)

    def submit_action_command(self, command: ActionCommand) -> None:
        runtime = self._active_runtime()
        if runtime.command_adapter is not None:
            return runtime.command_adapter.submit_action(command)
        if self.cfg.v2_writer_enabled:
            return CommandSubmissionReceipt("unavailable", SubmissionState.REJECTED, "command_backend_unavailable", uuid.uuid4().hex)
        self._one_shot_shadows[self.get_active_source()].observe(command)
        runtime.command_queue.put_action(command)

    def submit_latest_action_command(self, command: ActionCommand) -> None:
        """Replace any pending action of the same type with this one."""
        runtime = self._active_runtime()
        if runtime.command_adapter is not None:
            return runtime.command_adapter.submit_action(command)
        if self.cfg.v2_writer_enabled:
            return CommandSubmissionReceipt("unavailable", SubmissionState.REJECTED, "command_backend_unavailable", uuid.uuid4().hex)
        self._one_shot_shadows[self.get_active_source()].observe(command)
        runtime.command_queue.put_latest_action(command)

    def update_write_context(self, *, run_id: str | None, send_enabled: bool) -> None:
        for runtime in self.runtimes.values():
            assert runtime.command_context is not None
            context = runtime.command_context
            effective_run = run_id or "unauthorized"
            if context.run_id != effective_run:
                context.authorization_generation += 1
                context.run_id = effective_run
                context.execution_lease_id = effective_run
            if context.send_enabled != bool(send_enabled):
                context.send_generation += 1
                context.send_enabled = bool(send_enabled)
            if not context.send_enabled and runtime.command_broker is not None:
                _cancel_runtime(runtime,
                    CancelScope.SOURCE, source=runtime.name, emit_stop_barrier=True,
                    reason="system_send_disabled")

    def command_observation_candidates(self):
        runtime = self._active_runtime()
        return () if runtime.command_broker is None else runtime.command_broker.observation_candidates()

    def update_command_completion(self, command_id, state, reason_code: str) -> None:
        runtime = self._active_runtime()
        if runtime.command_broker is not None:
            runtime.command_broker.update_completion(command_id, state, reason_code)

    def command_status(self, command_id: str):
        runtime = self._active_runtime()
        if runtime.command_broker is None:
            raise KeyError(command_id)
        return runtime.command_broker.status(command_id)

    def submit_vehicle_command(self, envelope: VehicleCommandEnvelope) -> CommandSubmissionReceipt:
        runtime = self._active_runtime()
        broker = runtime.command_broker
        if broker is None:
            return CommandSubmissionReceipt(
                envelope.command_id, SubmissionState.REJECTED,
                "command_broker_unavailable", uuid.uuid4().hex,
            )
        receipt = broker.submit(envelope)
        if receipt.submission_state is SubmissionState.ACCEPTED and runtime.broker_worker is not None:
            runtime.broker_worker.notify()
        return receipt

    def cancel_vehicle_commands(self, request: CancelRequest) -> CancellationReceipt:
        runtime = self._active_runtime()
        broker = runtime.command_broker
        if broker is not None:
            return broker.cancel(request)
        disposition = (
            BarrierDisposition.STOP_UNDELIVERABLE
            if request.emit_stop_barrier else BarrierDisposition.NOT_REQUIRED
        )
        return CancellationReceipt(
            request.schema, request.cancellation_id, (), (),
            (request.run_id or request.command_id or request.execution_lease_id
             or request.stream_id or str(request.source or "unknown"),),
            None, disposition, request.source, None, time.monotonic_ns(),
            "command_broker_unavailable", uuid.uuid4().hex,
        )

    def cancel_run_commands(self, run_id: str) -> None:
        """Drain a paused run, including queued one-shot mode/land commands."""
        runtime = self._active_runtime()
        if runtime.command_broker is not None:
            _cancel_runtime(
                runtime, CancelScope.RUN, run_id=run_id,
                emit_stop_barrier=True, reason="flight_mode_not_guided",
            )
            return
        runtime.command_queue.clear_control()
        runtime.command_queue.clear_gimbal_rate()
        runtime.command_queue.clear_actions()
        self.stop_body_velocity_and_clear()

    def clear_pending_local_position_actions(self) -> None:
        """Remove queued navigation position targets so stale targets don't linger."""
        runtime = self._active_runtime()
        if runtime.command_broker is not None:
            _cancel_runtime(runtime,
                CancelScope.CONTINUOUS_STREAM, stream_id="navigation",
                reason="navigation_cleared")
            return
        runtime.command_queue.clear_actions(ActionType.LOCAL_POSITION)
        runtime.command_queue.clear_actions(ActionType.GLOBAL_GOTO)

    def hold_current_local_position(self, priority: int = 0) -> bool:
        """Send a LOCAL_POSITION hold at the current drone position.

        This overrides any stale LOCAL_POSITION target the flight controller
        may still be navigating toward.  Returns True if a hold was sent.
        """
        state = self.get_latest_drone_state()
        if not state.local_position_valid:
            self.logger.warning("hold_current_local_position skipped — no valid local position")
            return False
        yaw = state.yaw if state.attitude_valid else None
        receipt = self.local_position(
            x=state.local_x,
            y=state.local_y,
            z=state.local_z,
            frame=LOCAL_NED,
            yaw=yaw,
            priority=priority,
        )
        self.logger.info(
            "hold_current_local_position x=%.2f y=%.2f z=%.2f yaw=%s priority=%d",
            state.local_x, state.local_y, state.local_z,
            yaw, priority,
        )
        return getattr(getattr(receipt, "submission_state", None), "value", "ACCEPTED") == "ACCEPTED"

    def clear_continuous_commands(self) -> None:
        runtime = self._active_runtime()
        if runtime.command_broker is not None:
            _cancel_runtime(runtime,
                CancelScope.CONTINUOUS_STREAM, stream_id="all", emit_stop_barrier=True,
                reason="continuous_commands_cleared")
            return
        runtime.command_queue.clear_control()
        runtime.command_queue.clear_gimbal_rate()

    def set_mode(self, mode: str, priority: int = 5) -> None:
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.SET_MODE,
                params={"mode": mode},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def arm(self, priority: int = 1) -> None:
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.ARM,
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def disarm(self, priority: int = 1) -> None:
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.DISARM,
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def takeoff(self, altitude_m: float, priority: int = 2) -> None:
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.TAKEOFF,
                params={"altitude_m": float(altitude_m)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def land(self, priority: int = 2) -> None:
        runtime = self._active_runtime()
        runtime.command_queue.clear_control()
        runtime.command_queue.clear_gimbal_rate()
        runtime.command_queue.clear_actions(ActionType.LOCAL_POSITION)
        runtime.command_queue.clear_actions(ActionType.GLOBAL_GOTO)
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.LAND,
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def condition_yaw(
        self,
        yaw_deg: float,
        yaw_speed_deg_s: float = 20.0,
        direction: int = 0,
        relative: bool = False,
        priority: int = 4,
    ) -> None:
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.CONDITION_YAW,
                params={
                    "yaw_deg": float(yaw_deg),
                    "yaw_speed_deg_s": float(yaw_speed_deg_s),
                    "direction": int(direction),
                    "relative": bool(relative),
                },
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def change_speed(self, speed_mps: float, speed_type: int = 1, priority: int = 4) -> None:
        source_name = self.get_active_source()
        with self._speed_override_lock:
            self._speed_overrides[source_name][int(speed_type)] = float(speed_mps)
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.CHANGE_SPEED,
                params={"speed_mps": float(speed_mps), "speed_type": int(speed_type)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def _active_speed_overrides(self) -> list[dict[str, float | int]]:
        """Return the active source's transient flight-controller speed targets.

        ArduCopter can reset DO_CHANGE_SPEED when a Guided position setpoint
        changes the internal Guided submode.  Position commands carry this
        snapshot so CommandSender can reapply it immediately after that
        transition without bypassing the normal telemetry send path.
        """
        source_name = self.get_active_source()
        with self._speed_override_lock:
            overrides = dict(self._speed_overrides[source_name])
        return [
            {"speed_type": speed_type, "speed_mps": overrides[speed_type]}
            for speed_type in sorted(overrides)
        ]

    def set_home_current(self, priority: int = 4) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.SET_HOME,
                params={"current": True, "lat": 0.0, "lon": 0.0, "alt": 0.0},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def set_home_location(self, lat: float, lon: float, alt: float, priority: int = 4) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.SET_HOME,
                params={"current": False, "lat": float(lat), "lon": float(lon), "alt": float(alt)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def global_goto(
        self,
        lat: float,
        lon: float,
        alt: float,
        frame: int,
        priority: int = 4,
        yaw_rad: float | None = None,
        field_reference_version: object | None = None,
    ) -> None:
        params: dict[str, Any] = {"lat": float(lat), "lon": float(lon), "alt": float(alt), "frame": int(frame)}
        if yaw_rad is not None:
            params["yaw"] = float(yaw_rad)
        if field_reference_version is not None:
            params["field_reference_version"] = field_reference_version
        params["_speed_overrides"] = self._active_speed_overrides()
        return self.submit_latest_action_command(
            ActionCommand(
                action_type=ActionType.GLOBAL_GOTO,
                params=params,
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def local_position(
        self,
        x: float,
        y: float,
        z: float,
        frame: int,
        yaw: float | None = None,
        priority: int = 4,
    ) -> None:
        params = {"x": float(x), "y": float(y), "z": float(z), "frame": int(frame)}
        if yaw is not None:
            params["yaw"] = float(yaw)
        params["_speed_overrides"] = self._active_speed_overrides()
        return self.submit_latest_action_command(
            ActionCommand(
                action_type=ActionType.LOCAL_POSITION,
                params=params,
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def reposition(
        self,
        lat: float,
        lon: float,
        alt: float,
        ground_speed_mps: float = -1.0,
        yaw_deg: float | None = None,
        priority: int = 4,
    ) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.REPOSITION,
                params={
                    "lat": float(lat),
                    "lon": float(lon),
                    "alt": float(alt),
                    "ground_speed_mps": float(ground_speed_mps),
                    "yaw_deg": yaw_deg,
                },
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def set_roi_location(self, lat: float, lon: float, alt: float, priority: int = 4) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.SET_ROI_LOCATION,
                params={"lat": float(lat), "lon": float(lon), "alt": float(alt)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def roi_none(self, gimbal_device_id: int = 0, priority: int = 4) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.ROI_NONE,
                params={"gimbal_device_id": int(gimbal_device_id)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def gimbal_manager_configure(
        self,
        gimbal_device_id: int = 0,
        primary_sysid: int | None = None,
        primary_compid: int | None = None,
        priority: int = 4,
    ) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.GIMBAL_MANAGER_CONFIGURE,
                params={
                    "gimbal_device_id": int(gimbal_device_id),
                    "primary_sysid": primary_sysid,
                    "primary_compid": primary_compid,
                },
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def set_servo(self, channel: int, pwm: int, priority: int = 3) -> None:
        self.logger.info(
            "link_manager.set_servo channel=%s pwm=%s priority=%s",
            int(channel),
            int(pwm),
            int(priority),
        )
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.SET_SERVO,
                params={"channel": int(channel), "pwm": int(pwm)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def set_relay(self, relay_id: int, state: bool, priority: int = 3) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.SET_RELAY,
                params={"relay_id": int(relay_id), "state": bool(state)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def release_payload(self, payload_id: int, priority: int = 3) -> None:
        raise NotImplementedError(
            "release_payload is disabled; use set_servo_output_pwm(...) "
            "or set_servo(...) for MAV_CMD_DO_SET_SERVO payload release"
        )

    def request_message_interval(self, message_name: str, rate_hz: float, priority: int = 6) -> None:
        self.submit_action_command(
            ActionCommand(
                action_type=ActionType.REQUEST_MESSAGE_INTERVAL,
                params={"message_name": str(message_name), "rate_hz": float(rate_hz)},
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def send_gimbal_angle(
        self,
        pitch: float,
        yaw: float,
        roll: float = 0.0,
        mount_mode: int | None = None,
        priority: int = 5,
    ) -> None:
        return self.submit_action_command(
            ActionCommand(
                action_type=ActionType.GIMBAL_ANGLE,
                params={
                    "pitch": float(pitch),
                    "yaw": float(yaw),
                    "roll": float(roll),
                    "mount_mode": int(self.cfg.gimbal_mount_mode if mount_mode is None else mount_mode),
                },
                priority=priority,
                retries_left=self.cfg.action_cmd_retries,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    def send_gimbal_rate(
        self,
        yaw_rate: float,
        pitch_rate: float,
        yaw_lock: bool = False,
        gimbal_device_id: int = 0,
    ) -> None:
        runtime = self._active_runtime()
        command = GimbalRateCommand(
                yaw_rate=float(yaw_rate),
                pitch_rate=float(pitch_rate),
                yaw_lock=bool(yaw_lock),
                gimbal_device_id=int(gimbal_device_id),
                created_at=time.time(),
            )
        if runtime.command_adapter is not None:
            return runtime.command_adapter.submit_gimbal_rate(command)
        runtime.command_queue.put_gimbal_rate(command)

    def send_velocity_command(
        self,
        vx: float,
        vy: float,
        vz: float,
        frame: int = 1,
        yaw_rad: float | None = None,
        yaw_rate_rad_s: float | None = None,
    ) -> None:
        return self.submit_control_command(
            ControlCommand(
                command_type=ControlType.VELOCITY,
                vx=vx,
                vy=vy,
                vz=vz,
                yaw=yaw_rad,
                yaw_rate=yaw_rate_rad_s,
                timestamp=time.time(),
                frame=frame,
            )
        )

    def send_yaw_rate_command(self, yaw_rate: float, frame: int = 1) -> None:
        return self.submit_control_command(
            ControlCommand(
                command_type=ControlType.YAW_RATE,
                yaw_rate=yaw_rate,
                timestamp=time.time(),
                frame=frame,
            )
        )

    def stop_control(self, frame: int = 1) -> None:
        return self.submit_control_command(
            ControlCommand(
                command_type=ControlType.STOP,
                vx=0.0,
                vy=0.0,
                vz=0.0,
                yaw_rate=0.0,
                timestamp=time.time(),
                frame=frame,
            )
        )

    def stop_body_velocity(self) -> None:
        """Stop BODY_NED velocity control by sending zero body-frame velocity."""
        self.stop_control(frame=BODY_NED)

    def stop_body_velocity_and_clear(self) -> None:
        """Queue an explicit zero-velocity barrier before later actions.

        The barrier lives in the one-shot action queue at safety priority 0,
        so navigation/land handlers cannot erase it while clearing the latest
        continuous sample.  The sender transmits zero BODY_NED velocity and
        then clears any continuous sample that raced with the transition.
        """
        runtime = self._active_runtime()
        if runtime.command_broker is not None:
            return _cancel_runtime(runtime,
                CancelScope.CONTINUOUS_STREAM, stream_id="body", emit_stop_barrier=True,
                reason="stop_body_velocity_and_clear")
        runtime.command_queue.clear_control()
        self.submit_latest_action_command(
            ActionCommand(
                action_type=ActionType.STOP_BODY_VELOCITY,
                params={"frame": BODY_NED},
                priority=0,
                retries_left=0,
                retry_interval_sec=self.cfg.action_retry_interval_sec,
                created_at=time.time(),
            )
        )

    # ------------------------------------------------------------------
    # semantic wrappers (added T1 — zero behavioural change)
    # ------------------------------------------------------------------

    def goto_local_ned(
        self,
        x_north_m: float,
        y_east_m: float,
        z_down_m: float,
        yaw_rad: float | None = None,
        priority: int = 4,
    ) -> None:
        """Position target in LOCAL_NED frame.

        x_north_m  – metres North
        y_east_m   – metres East
        z_down_m   – metres Down (positive = down)
        yaw_rad    – optional target yaw in radians
        priority   – lower value = higher priority
        """
        self.local_position(
            x=x_north_m,
            y=y_east_m,
            z=z_down_m,
            frame=LOCAL_NED,
            yaw=yaw_rad,
            priority=priority,
        )

    def send_body_velocity(
        self,
        vx_forward_mps: float,
        vy_right_mps: float,
        vz_down_mps: float,
        yaw_rad: float | None = None,
        yaw_rate_rad_s: float | None = None,
    ) -> None:
        """Velocity command in BODY_NED (body-fixed) frame.

        vx_forward_mps – forward velocity (m/s)
        vy_right_mps   – right velocity (m/s)
        vz_down_mps    – down velocity (m/s)
        yaw_rad         – optional absolute yaw hold in radians
        yaw_rate_rad_s – optional yaw-rate target in rad/s
        """
        return self.send_velocity_command(
            vx=vx_forward_mps,
            vy=vy_right_mps,
            vz=vz_down_mps,
            frame=BODY_NED,
            yaw_rad=yaw_rad,
            yaw_rate_rad_s=yaw_rate_rad_s,
        )

    def set_servo_output_pwm(
        self,
        servo_output: int,
        pwm: int,
        priority: int = 3,
    ) -> None:
        """Set a flight-controller SERVO output PWM value.

        servo_output is a flight-controller SERVO output number,
        NOT an RC input channel.  This maps to MAV_CMD_DO_SET_SERVO.
        """
        return self.set_servo(
            channel=servo_output,
            pwm=pwm,
            priority=priority,
        )
