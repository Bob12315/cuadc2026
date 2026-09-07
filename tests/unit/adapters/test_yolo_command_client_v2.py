from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from application.yolo_command_client import YoloCommandClient, YoloCommandConfig
from contracts.platform.common import ClockStamp, SchemaVersion
from contracts.platform.perception import (
    SetTargetLock, VisionCommandEnvelope, VisionResultState,
)
from yolo_app.command_receiver import CommandReceiver


def test_client_retry_keeps_session_payload_and_applies_once() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    original_send = receiver._send_reply
    dropped = [2]
    def drop_first_pair(addr, payload):
        if dropped[0] > 0:
            dropped[0] -= 1
            return
        original_send(addr, payload)
    receiver._send_reply = drop_first_pair
    stop = threading.Event(); applied = []
    def serve() -> None:
        while not stop.is_set():
            for command in receiver.poll():
                applied.append(command.command_id)
                receiver.complete(command, applied=True, locked_track_id=7,
                                  recording_state="IDLE")
            stop.wait(0.001)
    thread = threading.Thread(target=serve); thread.start()
    calls = []
    def session_provider():
        calls.append(1)
        return "yolo-A" if len(calls) == 1 else "yolo-B"
    try:
        client = YoloCommandClient(YoloCommandConfig(host, port, True, 0.03, 1000, 2),
                                   session_provider)
        status = client.lock_target(7)
        assert status.state == VisionResultState.APPLIED and status.replayed is True
        assert len(applied) == 1 and len(calls) == 1
        assert client.status(status.command_id) == status
        command_id = "typed-port-command"
        envelope = VisionCommandEnvelope(
            SchemaVersion(2, 0), client.client_id, client.client_session_id,
            "yolo-A", command_id, 2, 1000,
            ClockStamp(datetime.now(timezone.utc), time.monotonic_ns(), client.client_session_id),
            SetTargetLock(7),
        )
        receipt = client.submit(envelope)
        assert receipt.command_id == command_id and receipt.result_state == VisionResultState.APPLIED
        assert client.status(command_id).state == VisionResultState.APPLIED
    finally:
        stop.set(); thread.join(1); receiver.close()
    assert not thread.is_alive()


def test_command_config_rejects_non_loopback() -> None:
    import pytest
    with pytest.raises(ValueError, match="loopback"):
        YoloCommandConfig("0.0.0.0", 5006, True)


def test_client_submits_virtual_nadir_reset() -> None:
    receiver = CommandReceiver("127.0.0.1", 0, process_session_id="yolo-A")
    host, port = receiver.sock.getsockname()
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            for command in receiver.poll():
                receiver.complete(command, applied=True, locked_track_id=None,
                                  recording_state="IDLE", reason_code="virtual_nadir_reset")
            stop.wait(0.001)

    thread = threading.Thread(target=serve); thread.start()
    try:
        client = YoloCommandClient(YoloCommandConfig(host, port, True, 0.1, 1000, 0),
                                   lambda: "yolo-A")
        status = client.reset_virtual_nadir()
        assert status.state is VisionResultState.APPLIED
        assert status.reason_code == "virtual_nadir_reset"
    finally:
        stop.set(); thread.join(1); receiver.close()
    assert not thread.is_alive()
