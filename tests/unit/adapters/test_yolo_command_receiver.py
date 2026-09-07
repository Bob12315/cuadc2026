from __future__ import annotations

import json
import socket
import time

from yolo_app.command_receiver import CommandReceiver


def test_command_receiver_accepts_recording_commands() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, enabled=True)
    assert receiver.sock is not None
    host, port = receiver.sock.getsockname()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        now = time.monotonic()
        sock.sendto(json.dumps({"schema_version": 1, "sequence": 1, "sent_at_monotonic": now,
                                "action": "recording_start"}).encode("utf-8"), (host, port))
        sock.sendto(json.dumps({"schema_version": 1, "sequence": 2, "sent_at_monotonic": now,
                                "action": "recording_stop"}).encode("utf-8"), (host, port))

    messages = receiver.poll()
    receiver.close()

    assert [message.action for message in messages] == ["recording_start", "recording_stop"]


def test_command_receiver_rejects_legacy_and_out_of_order_messages() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, enabled=True)
    assert receiver.sock is not None
    host, port = receiver.sock.getsockname()
    now = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(json.dumps({"action": "recording_start"}).encode(), (host, port))
        message = {"schema_version": 1, "sequence": 3, "sent_at_monotonic": now,
                   "action": "recording_stop"}
        sock.sendto(json.dumps(message).encode(), (host, port))
        sock.sendto(json.dumps(message).encode(), (host, port))
    assert [item.action for item in receiver.poll()] == ["recording_stop"]
    receiver.close()


def test_v2_command_session_dedupe_and_original_result_replay() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    payload = {"schema_major": 2, "message_type": "command", "client_id": "app",
        "client_session_id": "app-1", "command_id": "cmd-1", "ttl_ms": 1000,
        "sent_at_monotonic_ns": time.monotonic_ns(), "target_yolo_process_session_id": "yolo-A",
        "payload": {"kind": "set_target_lock", "track_id": 7}}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0)); sock.settimeout(0.5)
        sock.sendto(json.dumps(payload).encode(), (host, port))
        messages = receiver.poll()
        accepted = json.loads(sock.recvfrom(4096)[0])
        assert len(messages) == 1 and accepted["state"] == "ACCEPTED"
        receiver.complete(messages[0], applied=True, locked_track_id=7, recording_state="IDLE")
        applied = json.loads(sock.recvfrom(4096)[0])
        assert applied["state"] == "APPLIED" and applied["receipt_id"] == accepted["receipt_id"]
        sock.sendto(json.dumps(payload).encode(), (host, port))
        assert receiver.poll() == []
        duplicate = json.loads(sock.recvfrom(4096)[0])
        assert duplicate["state"] == "APPLIED" and duplicate["replayed"] is True
        assert duplicate["receipt_id"] == accepted["receipt_id"]
    receiver.close()


def test_v2_command_rejects_wrong_session_and_uses_receiver_local_ttl() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    base = {"schema_major": 2, "message_type": "command", "client_id": "app",
        "client_session_id": "app-1", "ttl_ms": 1, "payload": {"kind": "set_recording", "enabled": True}}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0)); sock.settimeout(0.5)
        wrong = base | {"command_id": "wrong", "sent_at_monotonic_ns": time.monotonic_ns(),
                        "target_yolo_process_session_id": "yolo-B"}
        sock.sendto(json.dumps(wrong).encode(), (host, port)); receiver.poll()
        first_wrong = json.loads(sock.recvfrom(4096)[0])
        assert first_wrong["reason_code"] == "SESSION_MISMATCH"
        sock.sendto(json.dumps(wrong).encode(), (host, port)); receiver.poll()
        replay_wrong = json.loads(sock.recvfrom(4096)[0])
        assert replay_wrong["receipt_id"] == first_wrong["receipt_id"] and replay_wrong["replayed"] is True
        old_foreign_clock = base | {"command_id": "old", "sent_at_monotonic_ns": 1,
                                    "target_yolo_process_session_id": "yolo-A"}
        sock.sendto(json.dumps(old_foreign_clock).encode(), (host, port))
        assert [message.command_id for message in receiver.poll()] == ["old"]
        assert json.loads(sock.recvfrom(4096)[0])["state"] == "ACCEPTED"
    receiver.close()


def test_v2_local_deadline_is_carried_to_application_before_side_effect() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    payload = {"schema_major": 2, "message_type": "command", "client_id": "app",
        "client_session_id": "app-1", "command_id": "expire", "ttl_ms": 10,
        "sent_at_monotonic_ns": 1, "target_yolo_process_session_id": "yolo-A",
        "payload": {"kind": "set_recording", "enabled": True}}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0)); sock.settimeout(0.5)
        sock.sendto(json.dumps(payload).encode(), (host, port)); commands = receiver.poll()
        assert len(commands) == 1; sock.recvfrom(4096)
        deadline = commands[0].expires_at_monotonic_ns
        assert deadline is not None and receiver.is_expired(commands[0], now_ns=deadline)
    receiver.close()


def test_v2_unsupported_command_returns_stable_rejection() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    payload = {"schema_major": 2, "message_type": "command", "client_id": "app",
        "client_session_id": "app-1", "command_id": "bad", "ttl_ms": 1000,
        "sent_at_monotonic_ns": 1, "target_yolo_process_session_id": "yolo-A",
        "payload": {"kind": "cycle_target"}}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0)); sock.settimeout(0.5)
        sock.sendto(json.dumps(payload).encode(), (host, port)); assert receiver.poll() == []
        rejected = json.loads(sock.recvfrom(4096)[0])
        assert rejected["state"] == "REJECTED" and rejected["reason_code"] == "UNSUPPORTED_COMMAND"
    receiver.close()


def test_v2_command_accepts_virtual_nadir_reset() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    payload = {"schema_major": 2, "message_type": "command", "client_id": "app",
        "client_session_id": "app-1", "command_id": "reset-1", "ttl_ms": 1000,
        "sent_at_monotonic_ns": time.monotonic_ns(), "target_yolo_process_session_id": "yolo-A",
        "payload": {"kind": "reset_virtual_nadir"}}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0)); sock.settimeout(0.5)
        sock.sendto(json.dumps(payload).encode(), (host, port))
        commands = receiver.poll()
        accepted = json.loads(sock.recvfrom(4096)[0])
        assert [command.action for command in commands] == ["reset_virtual_nadir"]
        assert accepted["state"] == "ACCEPTED"
    receiver.close()


def test_v2_same_command_id_with_different_payload_is_conflict() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    base = {"schema_major": 2, "message_type": "command", "client_id": "app",
        "client_session_id": "app-1", "command_id": "cmd-1", "ttl_ms": 1000,
        "sent_at_monotonic_ns": 1, "target_yolo_process_session_id": "yolo-A"}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0)); sock.settimeout(0.5)
        first = base | {"payload": {"kind": "set_target_lock", "track_id": 7}}
        sock.sendto(json.dumps(first).encode(), (host, port)); assert len(receiver.poll()) == 1
        accepted = json.loads(sock.recvfrom(4096)[0])
        changed = base | {"payload": {"kind": "set_target_lock", "track_id": 8}}
        sock.sendto(json.dumps(changed).encode(), (host, port)); assert receiver.poll() == []
        conflict = json.loads(sock.recvfrom(4096)[0])
        assert conflict["reason_code"] == "IDEMPOTENCY_CONFLICT"
        assert conflict["receipt_id"] != accepted["receipt_id"] and conflict["replayed"] is False
    receiver.close()
