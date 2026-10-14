from __future__ import annotations

import heapq
import threading
import uuid
from dataclasses import dataclass, replace
from typing import Callable

from contracts.platform.common import ExecutionFenceQueryPort

from contracts.platform.vehicle_commands import (
    BarrierDisposition, BodyVelocity, CancelRequest, CancelScope, CancellationReceipt,
    CommandStatusSnapshot, CommandSubmissionReceipt, CompletionState,
    QueueState, StopMotion, SubmissionState, TransportState, AckState,
    VehicleCommandEnvelope,
)
from .command_events import CommandEventRegistry


@dataclass(frozen=True, slots=True)
class _SafetyStopBarrier:
    barrier_id: str
    source: str
    link_session_id: str
    safety_generation: int
    deadline_monotonic_ns: int
    payload: StopMotion = StopMotion()


class CommandBroker:
    """Single owner for command admission, lifecycle and the final write gate."""

    def __init__(
        self,
        *,
        writer: object | None,
        source: Callable[[], str],
        link_session: Callable[[], str],
        authorization_generation: Callable[[], int],
        send_generation: Callable[[], int],
        monotonic_ns: Callable[[], int],
        connected: Callable[[], bool] = lambda: True,
        send_enabled: Callable[[], bool] = lambda: True,
        shadow: bool = False,
        event_capacity: int = 1000,
        field_version_matches: Callable[[object], bool] | None = None,
        execution_fence_query: ExecutionFenceQueryPort | None = None,
    ) -> None:
        self._writer = writer
        self._source = source
        self._link_session = link_session
        self._authorization_generation = authorization_generation
        self._send_generation = send_generation
        self._monotonic_ns = monotonic_ns
        self._connected = connected
        self._send_enabled = send_enabled
        self.shadow = shadow
        self.events = CommandEventRegistry(event_capacity)
        self._lock = threading.RLock()
        self._write_gate = threading.RLock()
        self._queue: list[tuple[int, int, str]] = []
        self._commands: dict[str, VehicleCommandEnvelope] = {}
        self._statuses: dict[str, CommandStatusSnapshot] = {}
        self._idempotency: dict[str, str] = {}
        self._latest: dict[str, str] = {}
        self._sequence = 0
        self._safety_generation = 0
        self._frozen = False
        self._active_motion: set[str] = set()
        self._field_version_matches = field_version_matches
        self._execution_fence_query = execution_fence_query
        self._submission_receipts: dict[str, CommandSubmissionReceipt] = {}
        self._cancellation_receipts: dict[str, CancellationReceipt] = {}
        self._cancellation_requests: dict[str, CancelRequest] = {}

    @property
    def write_count(self) -> int:
        return sum(1 for event in self.events.read_after(0, 100000) if event.event_type in {"TRANSMITTED", "BARRIER_TRANSMITTED"})

    def cancellation_fence(self) -> tuple[int, int, str]:
        """Return one broker-owned generation/session cut for a canonical cancel."""
        with self._lock:
            if self._execution_fence_query is not None:
                fence = self._execution_fence_query.snapshot()
                return (
                    int(fence.authorization_generation or 0),
                    int(fence.send_generation),
                    str(fence.link_session_id),
                )
            return (
                self._authorization_generation(),
                self._send_generation(),
                self._link_session(),
            )

    def submit(self, command: VehicleCommandEnvelope) -> CommandSubmissionReceipt:
        with self._lock:
            reason = self._admission_reason(command)
            if reason:
                self._record_rejected(command.command_id, reason)
                receipt = CommandSubmissionReceipt(command.command_id, SubmissionState.REJECTED, reason, uuid.uuid4().hex)
                self._submission_receipts[command.command_id] = receipt
                return receipt
            existing_id = self._idempotency.get(command.idempotency_key)
            if existing_id:
                existing = self._commands[existing_id]
                if existing == command:
                    original = self._submission_receipts[existing_id]
                    return replace(original, replayed=True,
                                   original_receipt_id=original.receipt_id)
                self._record_rejected(command.command_id, "idempotency_conflict")
                receipt = CommandSubmissionReceipt(command.command_id, SubmissionState.REJECTED,
                    "idempotency_conflict", uuid.uuid4().hex)
                self._submission_receipts[command.command_id] = receipt
                return receipt
            stream = self._stream_key(command)
            if stream:
                previous_id = self._latest.get(stream)
                if previous_id and self._status(previous_id).queue_state == QueueState.QUEUED:
                    self._transition(previous_id, queue_state=QueueState.SUPERSEDED, reason_code="superseded")
                    self.events.append(previous_id, "SUPERSEDED", self._monotonic_ns())
                self._latest[stream] = command.command_id
            self._sequence += 1
            self._commands[command.command_id] = command
            self._idempotency[command.idempotency_key] = command.command_id
            self._statuses[command.command_id] = CommandStatusSnapshot(
                command.command_id, SubmissionState.ACCEPTED, QueueState.QUEUED,
                TransportState.NOT_ATTEMPTED,
                AckState.NOT_EXPECTED if command.ack_policy.value == "DISABLED" else AckState.WAITING,
                CompletionState.NOT_OBSERVED, 1, "accepted",
            )
            heapq.heappush(self._queue, (command.priority, self._sequence, command.command_id))
            self.events.append(command.command_id, "QUEUED", self._monotonic_ns())
            receipt = CommandSubmissionReceipt(command.command_id, SubmissionState.ACCEPTED,
                                               "accepted", uuid.uuid4().hex)
            self._submission_receipts[command.command_id] = receipt
            return receipt

    def drain_one(self) -> CommandStatusSnapshot | None:
        with self._lock:
            command = None
            while self._queue:
                _, _, command_id = heapq.heappop(self._queue)
                if self._status(command_id).queue_state == QueueState.QUEUED:
                    command = self._commands[command_id]
                    self._transition(command_id, queue_state=QueueState.DEQUEUED, reason_code="dequeued")
                    self.events.append(command_id, "DEQUEUED", self._monotonic_ns())
                    break
            if command is None:
                return None
        self._checkpoint("after_dequeue")
        with self._write_gate:
            self._checkpoint("before_final_check")
            reason = self._final_reason(command)
            self._checkpoint("after_final_check")
            if reason:
                state = QueueState.EXPIRED if reason == "deadline_expired" else QueueState.CANCELLED
                self._transition(command.command_id, queue_state=state, reason_code=reason)
                self.events.append(command.command_id, reason.upper(), self._monotonic_ns())
                return self._status(command.command_id)
            if self.shadow:
                self._transition(command.command_id, reason_code="shadow_observed")
                self.events.append(command.command_id, "SHADOW_OBSERVED", self._monotonic_ns())
                return self._status(command.command_id)
            try:
                self._checkpoint("before_write")
                self._write(command)
                self._checkpoint("after_write")
            except Exception as exc:
                mark_failed = getattr(self._writer, "mark_write_failed", None)
                if callable(mark_failed):
                    mark_failed(command)
                self._transition(command.command_id, transport_state=TransportState.WRITE_FAILED, reason_code="write_failed")
                self.events.append(command.command_id, "WRITE_FAILED", self._monotonic_ns(), error=type(exc).__name__)
                return self._status(command.command_id)
            now = self._monotonic_ns()
            self._transition(command.command_id, transport_state=TransportState.TRANSMITTED,
                             transmitted_at_monotonic_ns=now, reason_code="transmitted")
            if isinstance(command.payload, BodyVelocity):
                self._active_motion.add(command.command_id)
            self.events.append(command.command_id, "TRANSMITTED", now)
            mark_transmitted = getattr(self._writer, "mark_transmitted", None)
            if callable(mark_transmitted):
                mark_transmitted(command)
            return self._status(command.command_id)

    def retry(self, command_id: str) -> CommandSubmissionReceipt:
        """Requeue the same immutable envelope without refreshing its TTL."""
        with self._lock:
            command = self._commands[command_id]
            reason = self._admission_reason(command)
            if reason:
                return CommandSubmissionReceipt(command_id, SubmissionState.REJECTED, reason, uuid.uuid4().hex)
            status = self._status(command_id)
            if status.transport_state == TransportState.TRANSMITTED:
                return CommandSubmissionReceipt(command_id, SubmissionState.REJECTED, "already_transmitted", uuid.uuid4().hex)
            self._sequence += 1
            self._transition(command_id, queue_state=QueueState.QUEUED,
                             transport_state=TransportState.NOT_ATTEMPTED, reason_code="retry_queued")
            heapq.heappush(self._queue, (command.priority, self._sequence, command_id))
            self.events.append(command_id, "RETRY_QUEUED", self._monotonic_ns())
            return CommandSubmissionReceipt(command_id, SubmissionState.ACCEPTED, "retry_queued", uuid.uuid4().hex)

    def cancel(self, request: CancelRequest) -> CancellationReceipt:
        with self._write_gate:
            existing = self._cancellation_receipts.get(request.cancellation_id)
            if existing is not None:
                if self._cancellation_requests[request.cancellation_id] != request:
                    return CancellationReceipt(
                        schema=request.schema, cancellation_id=request.cancellation_id,
                        matched_pending_ids=(), already_transmitted_ids=(),
                        not_found_ids=self._cancel_identifiers(request), barrier_id=None,
                        barrier_disposition=BarrierDisposition.NOT_REQUIRED,
                        source=request.source, link_session_id=self._link_session(),
                        completed_monotonic_ns=self._monotonic_ns(),
                        reason_code="cancellation_id_conflict", receipt_id=uuid.uuid4().hex,
                    )
                return replace(existing, replayed=True)
            invalid = self._cancel_reason(request)
            if invalid is not None:
                receipt = CancellationReceipt(
                    schema=request.schema, cancellation_id=request.cancellation_id,
                    matched_pending_ids=(), already_transmitted_ids=(),
                    not_found_ids=self._cancel_identifiers(request), barrier_id=None,
                    barrier_disposition=BarrierDisposition.NOT_REQUIRED,
                    source=request.source, link_session_id=self._link_session(),
                    completed_monotonic_ns=self._monotonic_ns(), reason_code=invalid,
                    receipt_id=uuid.uuid4().hex,
                )
                self._cancellation_receipts[request.cancellation_id] = receipt
                self._cancellation_requests[request.cancellation_id] = request
                return receipt
            self._frozen = True
            self._safety_generation += 1
            pending: list[str] = []
            transmitted: list[str] = []
            with self._lock:
                for command_id, command in self._commands.items():
                    if not self._matches(command, request):
                        continue
                    status = self._status(command_id)
                    if status.transport_state == TransportState.TRANSMITTED:
                        transmitted.append(command_id)
                    elif status.queue_state in {QueueState.QUEUED, QueueState.DEQUEUED}:
                        pending.append(command_id)
                        self._transition(command_id, queue_state=QueueState.CANCELLED, reason_code=request.reason_code)
                        self.events.append(command_id, "CANCELLED", self._monotonic_ns(), reason=request.reason_code)
            needs_barrier = request.emit_stop_barrier and bool(self._active_motion or request.stream_id)
            barrier_id = None
            disposition = BarrierDisposition.NOT_REQUIRED
            source = self._source()
            session = self._link_session()
            if needs_barrier:
                barrier_id = uuid.uuid4().hex
                if not self._connected():
                    disposition = BarrierDisposition.STOP_UNDELIVERABLE
                else:
                    barrier = _SafetyStopBarrier(barrier_id, source, session, self._safety_generation,
                                                 self._monotonic_ns() + 1_000_000_000)
                    try:
                        self._write(barrier)
                    except Exception:
                        disposition = BarrierDisposition.STOP_UNDELIVERABLE
                    else:
                        disposition = BarrierDisposition.TRANSMITTED
                        self.events.append(barrier_id, "BARRIER_TRANSMITTED", self._monotonic_ns(), source=source, session=session)
                self._active_motion.clear()
            self._frozen = False
            not_found = () if pending or transmitted or needs_barrier else self._cancel_identifiers(request)
            receipt = CancellationReceipt(
                schema=request.schema, cancellation_id=request.cancellation_id,
                matched_pending_ids=tuple(pending), already_transmitted_ids=tuple(transmitted),
                not_found_ids=not_found, barrier_id=barrier_id,
                barrier_disposition=disposition, source=source, link_session_id=session,
                completed_monotonic_ns=self._monotonic_ns(), reason_code=request.reason_code,
                receipt_id=uuid.uuid4().hex,
            )
            self._cancellation_receipts[request.cancellation_id] = receipt
            self._cancellation_requests[request.cancellation_id] = request
            return receipt

    def _cancel_reason(self, request: CancelRequest) -> str | None:
        if request.schema.major != 2 or not request.cancellation_id:
            return "invalid_cancel_contract"
        now = self._monotonic_ns()
        if request.deadline_monotonic_ns <= now: return "cancellation_deadline_expired"
        if self._execution_fence_query is not None:
            fence = self._execution_fence_query.snapshot()
            if (request.expected_send_generation is not None
                    and request.expected_send_generation != fence.send_generation):
                return "send_generation_mismatch"
            if request.cancellation_generation != fence.cancellation_generation:
                return "cancellation_generation_mismatch"
            if request.source is not None and request.source != fence.source:
                return "source_mismatch"
        else:
            if (request.expected_authorization_generation is not None
                    and request.expected_authorization_generation != self._authorization_generation()):
                return "authorization_generation_mismatch"
            if (request.expected_send_generation is not None
                    and request.expected_send_generation != self._send_generation()):
                return "send_generation_mismatch"
        if (request.expected_link_session_id is not None
                and request.expected_link_session_id != self._link_session()):
            return "session_mismatch"
        return None

    def status(self, command_id: str) -> CommandStatusSnapshot:
        with self._lock:
            return self._status(command_id)

    def observation_candidates(self) -> tuple[tuple[VehicleCommandEnvelope, CommandStatusSnapshot], ...]:
        with self._lock:
            return tuple(
                (command, self._status(command_id))
                for command_id, command in self._commands.items()
                if self._status(command_id).transport_state == TransportState.TRANSMITTED
                and self._status(command_id).completion_state == CompletionState.NOT_OBSERVED
                and command.completion_policy.value == "STATE_OBSERVED"
            )

    def update_ack(self, command_id: str, state: AckState, *, progress: int | None = None,
                   reason_code: str = "ack_updated") -> None:
        with self._lock:
            self._transition(command_id, ack_state=state, ack_progress=progress, reason_code=reason_code)
            self.events.append(command_id, state.value, self._monotonic_ns(), progress=progress)

    def update_completion(self, command_id: str, state: CompletionState, reason_code: str) -> None:
        with self._lock:
            status = self._status(command_id)
            if status.ack_state == AckState.NACKED and state == CompletionState.OBSERVED:
                return
            self._transition(command_id, completion_state=state, reason_code=reason_code)
            self.events.append(command_id, state.value, self._monotonic_ns())

    def _admission_reason(self, command: VehicleCommandEnvelope) -> str | None:
        if self._frozen: return "admission_frozen"
        if not self._send_enabled(): return "system_send_disabled"
        if command.source != self._source(): return "source_mismatch"
        if command.expected_link_session_id != self._link_session(): return "session_mismatch"
        if self._execution_fence_query is not None:
            fence = self._execution_fence_query.snapshot()
            if command.source != fence.source: return "execution_fence_source_mismatch"
            if command.expected_link_session_id != fence.link_session_id: return "execution_fence_session_mismatch"
            if command.run_id != fence.run_id: return "run_generation_mismatch"
            if command.run_execution_generation != fence.run_execution_generation: return "run_generation_mismatch"
            if command.execution_lease_id != fence.execution_lease_id: return "lease_generation_mismatch"
            if command.lease_generation != fence.lease_generation: return "lease_generation_mismatch"
            if command.authorization_generation != fence.authorization_generation: return "authorization_generation_mismatch"
            if command.send_generation != fence.send_generation: return "send_generation_mismatch"
        else:
            if command.authorization_generation != self._authorization_generation(): return "authorization_generation_mismatch"
            if command.send_generation != self._send_generation(): return "send_generation_mismatch"
        if command.deadline_monotonic_ns <= self._monotonic_ns(): return "deadline_expired"
        if command.field_reference_version is not None:
            if self._field_version_matches is None: return "field_version_checker_unavailable"
            try:
                if not self._field_version_matches(command.field_reference_version):
                    return "stale_field_reference_version"
            except Exception:
                return "field_version_checker_unavailable"
        return None

    def _final_reason(self, command: VehicleCommandEnvelope) -> str | None:
        status = self._status(command.command_id)
        if status.queue_state in {QueueState.CANCELLED, QueueState.SUPERSEDED, QueueState.EXPIRED}:
            return status.reason_code or status.queue_state.value.lower()
        return self._admission_reason(command)

    def _checkpoint(self, name: str) -> None:
        checkpoint = getattr(self._writer, "checkpoint", None)
        if callable(checkpoint):
            checkpoint(name)

    def _write(self, value: object) -> None:
        if self._writer is None:
            raise RuntimeError("writer_unavailable")
        write = getattr(self._writer, "write", None)
        if callable(write):
            write(value)
        elif callable(self._writer):
            self._writer(value)
        else:
            raise TypeError("writer is not callable")

    def _record_rejected(self, command_id: str, reason: str) -> None:
        self._statuses[command_id] = CommandStatusSnapshot(command_id, SubmissionState.REJECTED,
            QueueState.NOT_QUEUED, TransportState.NOT_ATTEMPTED, AckState.NOT_EXPECTED,
            CompletionState.NOT_OBSERVED, 1, reason)
        self.events.append(command_id, "REJECTED", self._monotonic_ns(), reason=reason)

    def _transition(self, command_id: str, **changes: object) -> None:
        current = self._status(command_id)
        self._statuses[command_id] = replace(current, revision=current.revision + 1, **changes)

    def _status(self, command_id: str) -> CommandStatusSnapshot:
        try: return self._statuses[command_id]
        except KeyError as exc: raise KeyError(f"unknown command: {command_id}") from exc

    @staticmethod
    def _stream_key(command: VehicleCommandEnvelope) -> str | None:
        if isinstance(command.payload, BodyVelocity): return f"body:{command.run_id}"
        if command.payload.kind in {"local_position", "global_position", "gimbal_rate"}:
            return f"{command.payload.kind}:{command.run_id}"
        return None

    @staticmethod
    def _matches(command: VehicleCommandEnvelope, request: CancelRequest) -> bool:
        if (request.target_run_execution_generation is not None
                and command.run_execution_generation != request.target_run_execution_generation):
            return False
        if (request.target_lease_generation is not None
                and command.lease_generation != request.target_lease_generation):
            return False
        stream_matches = (
            request.stream_id is None
            or request.stream_id in {"all", "body"} and isinstance(command.payload, BodyVelocity)
            or request.stream_id == "navigation" and command.payload.kind in {"local_position", "global_position"}
        )
        if request.scope == CancelScope.COMMAND: return command.command_id == request.command_id
        if request.scope == CancelScope.RUN: return command.run_id == request.run_id
        if request.scope == CancelScope.EXECUTION_LEASE: return command.execution_lease_id == request.execution_lease_id
        if request.scope == CancelScope.SOURCE: return command.source == request.source
        if request.scope == CancelScope.CONTINUOUS_STREAM: return stream_matches
        return False

    @staticmethod
    def _cancel_identifiers(request: CancelRequest) -> tuple[str, ...]:
        value = {
            CancelScope.COMMAND: request.command_id,
            CancelScope.RUN: request.run_id,
            CancelScope.EXECUTION_LEASE: request.execution_lease_id,
            CancelScope.SOURCE: request.source,
            CancelScope.CONTINUOUS_STREAM: request.stream_id,
        }[request.scope]
        return () if value is None else (str(value),)


class CommandBrokerWorker(threading.Thread):
    def __init__(self, broker: CommandBroker, *, idle_s: float = 0.01) -> None:
        super().__init__(name="CommandBrokerWriter", daemon=True)
        self.broker = broker
        self.idle_s = idle_s
        self._stop_event = threading.Event()
        self._wake = threading.Event()

    def notify(self) -> None:
        self._wake.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            status = self.broker.drain_one()
            if status is None:
                self._wake.wait(self.idle_s)
                self._wake.clear()

    def close(self, timeout_s: float = 1.0) -> bool:
        self._stop_event.set()
        self._wake.set()
        if self.ident is not None and self is not threading.current_thread():
            self.join(timeout_s)
        return not self.is_alive()
