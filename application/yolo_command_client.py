from __future__ import annotations

import itertools
import ipaddress
import json
import socket
import uuid
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from contracts.platform.perception import (
    RecordingState, ResetVirtualNadir, SetRecording, SetTargetLock, VisionCommandEnvelope,
    VisionCommandStatus, VisionResultState, VisionSubmissionReceipt,
)
from contracts.platform.common import ClockStamp, SchemaVersion
from contracts.perception_protocol import VisionCommand as LegacyVisionCommand

_SEQUENCE = itertools.count(1)


@dataclass(frozen=True, slots=True)
class YoloCommandConfig:
    ip: str = "127.0.0.1"
    port: int = 5006
    enabled: bool = True
    ack_timeout_s: float = 0.5
    ttl_ms: int = 1000
    ack_retries: int = 1

    def __post_init__(self) -> None:
        try: loopback = ipaddress.ip_address(self.ip).is_loopback
        except ValueError: loopback = self.ip == "localhost"
        if self.enabled and not loopback:
            raise ValueError("YOLO command endpoint must be loopback")
        if not 1 <= self.port <= 65_535 or self.ack_timeout_s <= 0 or self.ttl_ms <= 0:
            raise ValueError("invalid YOLO command transport bounds")


class YoloCommandClient:
    def __init__(self, config: YoloCommandConfig,
                 session_provider: Callable[[], str | None] | None = None) -> None:
        self.config = config
        self._session_provider = session_provider or (lambda: None)
        self.client_id = "uav-app"
        self.client_session_id = uuid.uuid4().hex
        self._statuses: OrderedDict[str, VisionCommandStatus] = OrderedDict()

    def send(self, action: str, track_id: int | None = None) -> VisionCommandStatus:
        """Explicit v1 fallback: delivery is UNKNOWN, never optimistic APPLIED."""
        if not self.config.enabled:
            raise RuntimeError("yolo command client is disabled")
        payload = LegacyVisionCommand(action=action, track_id=track_id,
                                      sequence=next(_SEQUENCE), sent_at_monotonic=time.monotonic()).to_dict()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(json.dumps(payload).encode(), (self.config.ip, self.config.port))
        return self._remember(VisionCommandStatus(uuid.uuid4().hex, VisionResultState.ACCEPTED, False,
            "legacy_delivery_unknown", track_id, RecordingState.UNKNOWN,
            None, None, 0, None))

    def submit(self, envelope: VisionCommandEnvelope) -> VisionSubmissionReceipt:
        status = self._submit_status(envelope)
        return VisionSubmissionReceipt(
            status.receipt_id or f"client:{status.command_id}",
            status.command_id,
            status.state,
            status.reason_code,
            status.replayed,
        )

    def _submit_status(self, envelope: VisionCommandEnvelope) -> VisionCommandStatus:
        if envelope.client_id != self.client_id or envelope.client_session_id != self.client_session_id:
            return self._remember(VisionCommandStatus(
                envelope.command_id, VisionResultState.REJECTED, False,
                "client_session_mismatch", None, RecordingState.UNKNOWN,
                None, None, 0, None,
            ))
        target_session = envelope.target_yolo_process_session_id
        command_id = envelope.command_id
        sent_at_monotonic_ns = envelope.sent_at.monotonic_ns
        local_started_at_monotonic_ns = time.monotonic_ns()
        command = envelope.command
        body = {"kind": command.kind}
        if isinstance(command, SetTargetLock):
            body["track_id"] = command.track_id
        elif isinstance(command, SetRecording):
            body["enabled"] = command.enabled
        payload = {
            "schema_major": 2, "schema_minor": 0, "message_type": "command",
            "sequence": envelope.sequence, "ttl_ms": envelope.ttl_ms,
            "sent_at_monotonic_ns": sent_at_monotonic_ns,
            "sent_at_utc": envelope.sent_at.utc.isoformat(),
            "client_id": envelope.client_id, "client_session_id": envelope.client_session_id,
            "target_yolo_process_session_id": target_session,
            "command_id": command_id, "payload": body,
        }
        if not self.config.enabled:
            raise RuntimeError("yolo command client is disabled")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            latest = None
            for _attempt in range(max(0, self.config.ack_retries) + 1):
                if time.monotonic_ns() - local_started_at_monotonic_ns > envelope.ttl_ms * 1_000_000:
                    break
                sock.sendto(encoded, (self.config.ip, self.config.port))
                deadline = time.monotonic() + self.config.ack_timeout_s
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    sock.settimeout(remaining)
                    try:
                        raw, _ = sock.recvfrom(4096)
                    except socket.timeout:
                        break
                    try: reply = json.loads(raw.decode())
                    except (UnicodeDecodeError, json.JSONDecodeError): continue
                    if (reply.get("schema_major") != 2 or reply.get("message_type") != "ack"
                            or reply.get("command_id") != command_id
                            or reply.get("client_id") != self.client_id
                            or reply.get("client_session_id") != self.client_session_id
                            or reply.get("yolo_process_session_id") != target_session):
                        continue
                    latest = self._status(reply)
                    if latest.state in {VisionResultState.APPLIED, VisionResultState.REJECTED, VisionResultState.EXPIRED}:
                        return self._remember(latest)
        if latest is not None:
            return self._remember(latest)
        requested = RecordingState.START_REQUESTED if isinstance(command, SetRecording) and command.enabled else (
            RecordingState.STOP_REQUESTED if isinstance(command, SetRecording) else RecordingState.UNKNOWN)
        return self._remember(VisionCommandStatus(command_id, VisionResultState.IN_PROGRESS, False,
            "vision_ack_timeout", None, requested, None, None, 0, None)
        )

    def lock_target(self, track_id: int) -> VisionCommandStatus:
        session = self._session_provider()
        return (self._submit_typed(SetTargetLock(int(track_id)), session) if session
                else self.send("lock_target", int(track_id)))

    def unlock_target(self) -> VisionCommandStatus:
        session = self._session_provider()
        return self._submit_typed(SetTargetLock(None), session) if session else self.send("unlock_target")

    def start_recording(self) -> VisionCommandStatus:
        session = self._session_provider()
        return self._submit_typed(SetRecording(True), session) if session else self.send("recording_start")

    def stop_recording(self) -> VisionCommandStatus:
        session = self._session_provider()
        return self._submit_typed(SetRecording(False), session) if session else self.send("recording_stop")

    def reset_virtual_nadir(self) -> VisionCommandStatus:
        session = self._session_provider()
        return (self._submit_typed(ResetVirtualNadir(), session) if session
                else self.send("reset_virtual_nadir"))

    def _submit_typed(self, command: SetTargetLock | SetRecording | ResetVirtualNadir,
                      target_session: str) -> VisionCommandStatus:
        envelope = VisionCommandEnvelope(
            SchemaVersion(2, 0), self.client_id, self.client_session_id,
            target_session, uuid.uuid4().hex, next(_SEQUENCE), self.config.ttl_ms,
            ClockStamp(datetime.now(timezone.utc), time.monotonic_ns(), self.client_session_id),
            command,
        )
        receipt = self.submit(envelope)
        return self.status(receipt.command_id)

    def status(self, command_id: str) -> VisionCommandStatus:
        try:
            return self._statuses[command_id]
        except KeyError as exc:
            raise KeyError(f"unknown vision command: {command_id}") from exc

    def _remember(self, status: VisionCommandStatus) -> VisionCommandStatus:
        self._statuses[status.command_id] = status
        self._statuses.move_to_end(status.command_id)
        while len(self._statuses) > 512:
            self._statuses.popitem(last=False)
        return status

    @staticmethod
    def _status(data: dict[str, object]) -> VisionCommandStatus:
        try: state = VisionResultState(str(data.get("state")))
        except ValueError: state = VisionResultState.REJECTED
        try: recording = RecordingState(str(data.get("recording_state", "UNKNOWN")))
        except ValueError: recording = RecordingState.UNKNOWN
        return VisionCommandStatus(str(data.get("command_id", "")), state,
            bool(data.get("duplicate", False)), str(data.get("reason_code", state.value.lower())),
            None if data.get("locked_track_id") is None else int(data["locked_track_id"]),
            recording, None if data.get("recorder_session_id") is None else str(data["recorder_session_id"]),
            None if data.get("actual_path") is None else str(data["actual_path"]),
            int(data.get("frames", 0)), None if data.get("error") is None else str(data["error"]),
            None, str(data.get("receipt_id", "")), bool(data.get("replayed", False)),
            None if data.get("recorder_boot_id") is None else str(data["recorder_boot_id"]))
