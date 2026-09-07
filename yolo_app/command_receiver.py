from __future__ import annotations

import json
import socket
import time
import uuid
from collections import OrderedDict
from contracts.perception_protocol import COMMAND_SCHEMA_VERSION

try:
    from .models import CommandMessage
except ImportError:
    from models import CommandMessage


class CommandReceiver:
    """
    Lightweight UDP command receiver.

    Accepted JSON examples:
    {"action": "switch_next"}
    {"action": "switch_prev"}
    {"action": "unlock_target"}
    {"action": "lock_target", "track_id": 7}
    {"action": "recording_start"}
    {"action": "recording_stop"}
    {"action": "reset_virtual_nadir"}
    """

    def __init__(self, ip: str, port: int, enabled: bool = True,
                 process_session_id: str | None = None, dedupe_capacity: int = 512,
                 dedupe_ttl_ms: int = 60_000) -> None:
        self.enabled = enabled
        self.sock: socket.socket | None = None
        self._last_sequence = -1
        self.process_session_id = process_session_id
        self._dedupe_capacity = dedupe_capacity
        self._dedupe_ttl_ms = max(1, int(dedupe_ttl_ms))
        self._results: OrderedDict[
            tuple[str, str, str], tuple[str, int, int, dict[str, object]]
        ] = OrderedDict()
        self._actual_state: dict[str, object] = {
            "locked_track_id": None, "recording_state": "UNKNOWN",
            "recorder_boot_id": None, "recorder_session_id": None,
            "actual_path": None, "frames": 0, "error": None,
        }
        if enabled:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind((ip, port))
            self.sock.setblocking(False)

    def poll(self) -> list[CommandMessage]:
        if not self.enabled or self.sock is None:
            return []

        messages: list[CommandMessage] = []
        while True:
            try:
                payload, addr = self.sock.recvfrom(4096)
            except BlockingIOError:
                break

            try:
                decoded = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            if decoded.get("schema_major") == 2:
                message = self._decode_v2(decoded, addr)
                if message is not None:
                    messages.append(message)
                continue
            if decoded.get("schema_version") != COMMAND_SCHEMA_VERSION:
                continue
            try:
                sequence = int(decoded["sequence"])
                sent_at = float(decoded["sent_at_monotonic"])
            except (KeyError, TypeError, ValueError):
                continue
            if sequence <= self._last_sequence or time.monotonic() - sent_at > 2.0:
                continue
            action = decoded.get("action")
            if action not in {
                "lock_target",
                "switch_next",
                "switch_prev",
                "unlock_target",
                "recording_start",
                "recording_stop",
                "reset_virtual_nadir",
            }:
                continue
            self._last_sequence = sequence
            track_id = decoded.get("track_id")
            if track_id is not None:
                try:
                    track_id = int(track_id)
                except (TypeError, ValueError):
                    track_id = None
            messages.append(CommandMessage(action=action, track_id=track_id))
        return messages

    def _decode_v2(self, decoded: dict[str, object], addr: tuple[str, int]) -> CommandMessage | None:
        if decoded.get("message_type") != "command":
            return None
        try:
            client_id = str(decoded["client_id"]); client_session = str(decoded["client_session_id"])
            command_id = str(decoded["command_id"]); ttl_ms = int(decoded["ttl_ms"])
            body = decoded["payload"]
        except (KeyError, TypeError, ValueError):
            return None
        received_at_ns = time.monotonic_ns()
        self._purge_expired(received_at_ns)
        fingerprint = json.dumps(decoded, sort_keys=True, separators=(",", ":"))
        if not all((client_id, client_session, command_id)) or ttl_ms <= 0 or not isinstance(body, dict):
            return None
        key = (client_id, client_session, command_id)
        cached = self._results.get(key)
        if cached is not None:
            cached_fingerprint, _received_ns, _ttl_ms, cached_payload = cached
            if cached_fingerprint != fingerprint:
                conflict = dict(cached_payload)
                conflict.update(state="REJECTED", reason_code="IDEMPOTENCY_CONFLICT",
                                receipt_id=uuid.uuid4().hex, replayed=False, duplicate=False)
                self._send_reply(addr, conflict)
                return None
            replay = dict(cached_payload)
            replay.update(self._actual_state)
            replay["replayed"] = True; replay["duplicate"] = True
            self._send_reply(addr, replay)
            return None
        if decoded.get("target_yolo_process_session_id") != self.process_session_id:
            rejected = self._result(client_id, client_session, command_id,
                                    "REJECTED", "SESSION_MISMATCH")
            self._store(key, fingerprint, received_at_ns, ttl_ms, rejected)
            self._send_reply(addr, rejected)
            return None
        kind = body.get("kind")
        if kind == "set_target_lock":
            track_id = body.get("track_id")
            action = "unlock_target" if track_id is None else "lock_target"
            try:
                normalized_track_id = None if track_id is None else int(track_id)
            except (TypeError, ValueError):
                rejected = self._result(client_id, client_session, command_id,
                                        "REJECTED", "INVALID_TRACK_ID")
                self._store(key, fingerprint, received_at_ns, ttl_ms, rejected)
                self._send_reply(addr, rejected)
                return None
            message = CommandMessage(action, normalized_track_id, command_id,
                                     client_id, client_session, addr, True,
                                     received_at_ns + ttl_ms * 1_000_000)
            self._claim(key, message, fingerprint, received_at_ns, ttl_ms)
            return message
        if kind == "set_recording" and isinstance(body.get("enabled"), bool):
            message = CommandMessage("recording_start" if body["enabled"] else "recording_stop", None,
                                     command_id, client_id, client_session, addr, True,
                                     received_at_ns + ttl_ms * 1_000_000)
            self._claim(key, message, fingerprint, received_at_ns, ttl_ms)
            return message
        if kind == "reset_virtual_nadir":
            message = CommandMessage("reset_virtual_nadir", None, command_id,
                                     client_id, client_session, addr, True,
                                     received_at_ns + ttl_ms * 1_000_000)
            self._claim(key, message, fingerprint, received_at_ns, ttl_ms)
            return message
        rejected = self._result(client_id, client_session, command_id,
                                "REJECTED", "UNSUPPORTED_COMMAND")
        self._store(key, fingerprint, received_at_ns, ttl_ms, rejected)
        self._send_reply(addr, rejected)
        return None

    def _claim(self, key: tuple[str, str, str], command: CommandMessage,
               fingerprint: str, received_at_ns: int, ttl_ms: int) -> None:
        accepted = {
            "schema_major": 2, "schema_minor": 0, "message_type": "ack",
            "producer_id": "rk3588-yolo", "yolo_process_session_id": self.process_session_id,
            "client_id": command.client_id, "client_session_id": command.client_session_id,
            "command_id": command.command_id, "state": "ACCEPTED", "reason_code": "accepted",
            "receipt_id": uuid.uuid4().hex, "replayed": False, "duplicate": False,
            "locked_track_id": None, "recording_state": "UNKNOWN",
            "recorder_boot_id": None, "recorder_session_id": None,
            "actual_path": None, "frames": 0, "error": None,
        }
        accepted.update(self._actual_state)
        self._store(key, fingerprint, received_at_ns, ttl_ms, accepted)
        self._send_reply(command.reply_addr, accepted)

    def complete(self, command: CommandMessage, *, applied: bool, locked_track_id: int | None,
                 recording_state: str, recorder_session_id: str | None = None,
                 recorder_boot_id: str | None = None, actual_path: str | None = None,
                 frames: int = 0, error: str | None = None,
                 result_state: str | None = None, reason_code: str | None = None) -> None:
        if not command.v2 or command.reply_addr is None or command.command_id is None:
            return
        payload: dict[str, object] = {
            "schema_major": 2, "schema_minor": 0, "message_type": "ack",
            "producer_id": "rk3588-yolo", "yolo_process_session_id": self.process_session_id,
            "client_id": command.client_id, "client_session_id": command.client_session_id,
            "command_id": command.command_id,
            "state": result_state or ("APPLIED" if applied else "REJECTED"),
            "reason_code": reason_code or ("applied" if applied else "apply_failed"),
            "receipt_id": "", "replayed": False, "duplicate": False,
            "locked_track_id": locked_track_id, "recording_state": recording_state,
            "recorder_boot_id": recorder_boot_id, "recorder_session_id": recorder_session_id,
            "actual_path": actual_path,
            "frames": frames, "error": error,
        }
        key = (str(command.client_id), str(command.client_session_id), command.command_id)
        cached = self._results.get(key)
        if cached is None:
            return
        self._actual_state.update({
            "locked_track_id": locked_track_id, "recording_state": recording_state,
            "recorder_boot_id": recorder_boot_id, "recorder_session_id": recorder_session_id,
            "actual_path": actual_path, "frames": frames, "error": error,
        })
        fingerprint, received_at_ns, ttl_ms, accepted = cached
        payload["receipt_id"] = accepted["receipt_id"]
        self._store(key, fingerprint, received_at_ns, ttl_ms, payload)
        self._send_reply(command.reply_addr, payload)

    @staticmethod
    def is_expired(command: CommandMessage, *, now_ns: int | None = None) -> bool:
        if command.expires_at_monotonic_ns is None:
            return False
        return (time.monotonic_ns() if now_ns is None else now_ns) >= command.expires_at_monotonic_ns

    def _store(self, key: tuple[str, str, str], fingerprint: str,
               received_at_ns: int, ttl_ms: int, payload: dict[str, object]) -> None:
        self._results[key] = (fingerprint, received_at_ns,
                              max(ttl_ms, self._dedupe_ttl_ms), dict(payload))
        self._results.move_to_end(key)
        while len(self._results) > self._dedupe_capacity:
            self._results.popitem(last=False)

    def _purge_expired(self, now_ns: int) -> None:
        expired = [key for key, (_fingerprint, received_ns, ttl_ms, _payload) in self._results.items()
                   if now_ns - received_ns > ttl_ms * 1_000_000]
        for key in expired:
            self._results.pop(key, None)

    def _send_reply(self, addr: tuple[str, int], payload: dict[str, object]) -> None:
        if self.sock is not None:
            self.sock.sendto(json.dumps(payload, separators=(",", ":")).encode(), addr)

    def _result(self, client_id: str, client_session: str, command_id: str,
                state: str, reason_code: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_major": 2, "schema_minor": 0, "message_type": "ack",
            "producer_id": "rk3588-yolo", "yolo_process_session_id": self.process_session_id,
            "client_id": client_id, "client_session_id": client_session,
            "command_id": command_id, "state": state, "reason_code": reason_code,
            "receipt_id": uuid.uuid4().hex, "replayed": False, "duplicate": False,
        }
        payload.update(self._actual_state)
        return payload

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
