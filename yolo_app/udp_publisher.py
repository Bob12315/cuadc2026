from __future__ import annotations

import json
import socket
import time
import uuid
from datetime import datetime, timezone

try:
    from .models import CurrentTarget, SceneDetections
except ImportError:
    from models import CurrentTarget, SceneDetections


class UdpPublisher:
    HELLO_INTERVAL_NS = 1_000_000_000

    def __init__(self, udp_ip: str, udp_port: int, *, max_datagram_bytes: int = 60_000,
                 max_detections: int = 128) -> None:
        self.addr = (udp_ip, udp_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.producer_id = "rk3588-yolo"
        self.process_session_id = uuid.uuid4().hex
        self.clock_domain_id = uuid.uuid4().hex
        if not 512 <= int(max_datagram_bytes) <= 65_507 or not 1 <= int(max_detections) <= 4096:
            raise ValueError("invalid YOLO UDP bounds")
        self.max_datagram_bytes = int(max_datagram_bytes)
        self.max_detections = int(max_detections)
        self._last_hello_monotonic_ns: int | None = None
        self._sequence = 0

    def publish(self, target: CurrentTarget, scene: SceneDetections | None = None,
                recorder_status: object | None = None,
                captured_at_monotonic_ns: int | None = None) -> None:
        now_monotonic_ns = time.monotonic_ns()
        if (self._last_hello_monotonic_ns is None
                or now_monotonic_ns - self._last_hello_monotonic_ns >= self.HELLO_INTERVAL_NS):
            self._send_hello(now_monotonic_ns)
        scene_data = scene.to_dict() if scene is not None else {}
        if scene is not None and (
            int(scene_data.get("frame_id", -1)) != int(target.frame_id)
            or int(scene_data.get("image_width", -1)) != int(target.image_width)
            or int(scene_data.get("image_height", -1)) != int(target.image_height)
        ):
            raise ValueError("target and detections must come from the same frame")
        detections = list(scene_data.get("detections", []))
        detections.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        original_count = len(detections)
        detections = detections[:self.max_detections]
        captured_at_monotonic_ns = (now_monotonic_ns if captured_at_monotonic_ns is None
                                    else max(0, int(captured_at_monotonic_ns)))
        body = {"frame_id": target.frame_id, "captured_at_monotonic_ns": captured_at_monotonic_ns,
                "image_width_px": target.image_width, "image_height_px": target.image_height,
                "target": target.to_dict() if target.target_valid else None, "detections": detections,
                "original_detection_count": original_count, "truncated": original_count > len(detections),
                "producer_status": "ok", "timestamp": scene_data.get("timestamp", target.timestamp)}
        if recorder_status is not None:
            expires = getattr(recorder_status, "expires_at_monotonic", None)
            body.update({
                "recording_state": str(getattr(recorder_status, "state", "UNKNOWN")),
                "recorder_boot_id": getattr(recorder_status, "recorder_boot_id", None),
                "recorder_session_id": getattr(recorder_status, "recorder_session_id", None),
                "recording_path": getattr(recorder_status, "path", None) or None,
                "recording_frames": int(getattr(recorder_status, "frames", 0)),
                "recording_error": getattr(recorder_status, "error", None) or None,
                "recording_expires_at_monotonic_ns": None if expires is None else int(float(expires) * 1_000_000_000),
            })
        self._sequence += 1
        data = {"schema_major": 2, "schema_minor": 0, "message_type": "perception",
                "sequence": self._sequence, "sent_at_utc": datetime.now(timezone.utc).isoformat(),
                "sent_at_monotonic_ns": now_monotonic_ns, "ttl_ms": 2000,
                "producer_id": self.producer_id, "yolo_process_session_id": self.process_session_id,
                "producer_clock_domain_id": self.clock_domain_id, "payload": body}
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        while len(payload) > self.max_datagram_bytes and body["detections"]:
            body["detections"].pop()
            body["truncated"] = True
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > self.max_datagram_bytes:
            raise ValueError("perception datagram exceeds max_datagram_bytes")
        self.sock.sendto(payload, self.addr)

    def _send(self, data: dict[str, object]) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > self.max_datagram_bytes:
            raise ValueError("hello datagram exceeds max_datagram_bytes")
        self.sock.sendto(payload, self.addr)

    def _send_hello(self, now_monotonic_ns: int) -> None:
        self._send({"schema_major": 2, "schema_minor": 0, "message_type": "hello",
                    "sequence": 0, "sent_at_utc": datetime.now(timezone.utc).isoformat(),
                    "sent_at_monotonic_ns": now_monotonic_ns, "ttl_ms": 2000,
                    "producer_id": self.producer_id, "yolo_process_session_id": self.process_session_id,
                    "producer_clock_domain_id": self.clock_domain_id,
                    "payload": {"capabilities": ["perception_v2", "vision_command_v2", "recording_status_v2"]}})
        self._last_hello_monotonic_ns = now_monotonic_ns

    def close(self) -> None:
        self.sock.close()
