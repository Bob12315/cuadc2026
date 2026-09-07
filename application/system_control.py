from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Callable

from application.send_state import SystemSendState
from application.yolo_command_client import YoloCommandClient
from contracts.action import OperationResult


class SystemControlService:
    """Administrative controls with mandatory authorization revocation and queue cleanup."""

    def __init__(
        self, *, send_state: SystemSendState, get_link: Callable[[], Any],
        action_runtime: Any, yolo_command_config: Any,
        restart_commands: dict[str, list[str]], record_event: Callable[[str, str], None],
        reconnect_telemetry: Callable[[], Any] | None = None,
        get_perception_snapshot: Callable[[], Any] | None = None,
        get_yolo_session_id: Callable[[], str | None] | None = None,
        video_dir: str | Path = "runtime/videos",
    ) -> None:
        self.send_state = send_state
        self._get_link = get_link
        self._runtime = action_runtime
        self._yolo_cfg = yolo_command_config
        self._restart_commands = restart_commands
        self._record_event = record_event
        self._reconnect_telemetry = reconnect_telemetry
        self._video_dir = Path(video_dir)
        self._get_perception_snapshot = get_perception_snapshot or (lambda: None)
        self._get_yolo_session_id = get_yolo_session_id or (lambda: None)
        self._yolo_client = YoloCommandClient(self._yolo_cfg, self._get_yolo_session_id)
        self._recording = {"recording": False, "recording_state": "UNKNOWN", "path": "", "message": "状态未知"}
        self._processes: dict[str, subprocess.Popen] = {}

    def _revoke_and_clear(self, reason: str) -> Any:
        decision = self._runtime.dispatcher.clear_authorization(reason)
        self._runtime.clear_navigation_queue(self._get_link())
        return decision

    def set_send(self, enabled: bool) -> OperationResult:
        snapshot = self.send_state.set_send_commands(bool(enabled))
        if not snapshot.send_commands:
            self._revoke_and_clear("system_send_disabled")
        message = f"system SEND={'ON' if snapshot.send_commands else 'OFF'}"
        self._record_event("SAFETY", message)
        return OperationResult(True, message)

    def disable_send(self, reason: str) -> Any:
        self.send_state.set_send_commands(False)
        decision = self._revoke_and_clear(reason)
        self._record_event("SAFETY", f"automatic command sending disabled: {reason}")
        return decision

    def switch_source(self, source: str) -> OperationResult:
        manager = self._get_link()
        if manager is None:
            return OperationResult(False, "telemetry is not connected")
        stop_decision = self.disable_send("source_switch")
        snapshot = manager.get_link_control_snapshot()
        receipt = manager.activate_source(source, snapshot.revision)
        if not receipt.accepted:
            return OperationResult(False, f"source switch rejected: {source}")
        stop_disposition = getattr(stop_decision, "barrier_disposition", None)
        if stop_disposition == "STOP_UNDELIVERABLE" or receipt.barrier_disposition == "STOP_UNDELIVERABLE":
            return OperationResult(True,
                f"active telemetry source={source}; SEND remains OFF; old-source STOP_UNDELIVERABLE")
        return OperationResult(True, f"active telemetry source={source}; SEND remains OFF")

    def active_source(self, default: str = "real") -> str:
        manager = self._get_link()
        getter = getattr(manager, "get_active_source", None)
        return str(getter()) if callable(getter) else str(default)

    def reconnect(self) -> OperationResult:
        self.disable_send("telemetry_reconnect")
        if not callable(self._reconnect_telemetry):
            return OperationResult(False, "telemetry reconnect is unavailable")
        self._reconnect_telemetry()
        return OperationResult(True, "telemetry reconnect requested; SEND remains OFF")

    def target_command(self, command: str) -> OperationResult:
        parts = command.split()
        try:
            if len(parts) == 3 and parts[1] == "lock": status = self._yolo_client.lock_target(int(parts[2]))
            elif len(parts) >= 2 and parts[1] == "unlock": status = self._yolo_client.unlock_target()
            elif len(parts) >= 2 and parts[1] in {"next", "prev", "previous"}:
                snapshot = self._get_perception_snapshot()
                detections = tuple(getattr(snapshot, "detections", ()))
                track_ids = sorted({int(item.track_id) for item in detections if item.track_id is not None})
                if not track_ids: return OperationResult(False, "no tracked YOLO targets available")
                current = getattr(getattr(snapshot, "target", None), "track_id", None)
                index = track_ids.index(current) if current in track_ids else (-1 if parts[1] == "next" else 0)
                offset = 1 if parts[1] == "next" else -1
                status = self._yolo_client.lock_target(track_ids[(index + offset) % len(track_ids)])
            else: return OperationResult(False, "format: target <next|prev|lock <track_id>|unlock>")
        except Exception as exc:
            return OperationResult(False, f"target command failed: {exc}")
        ok = status.state.value in {"ACCEPTED", "IN_PROGRESS", "APPLIED"}
        result = OperationResult(ok, f"{command}: {status.state.value}/{status.reason_code}")
        self._record_event("OK", result.message)
        return result

    def reset_virtual_nadir(self) -> OperationResult:
        # The reference change invalidates every image-domain target.  Revoke
        # the current Action authorization before requesting the reset so an
        # active visual action cannot continue on a mixed coordinate system.
        self._revoke_and_clear("virtual_nadir_reset")
        try:
            status = self._yolo_client.reset_virtual_nadir()
        except Exception as exc:
            result = OperationResult(False, f"virtual nadir reset failed: {exc}")
            self._record_event("ERROR", result.message)
            return result
        ok = status.state.value in {"ACCEPTED", "IN_PROGRESS", "APPLIED"}
        result = OperationResult(ok, f"virtual nadir reset: {status.state.value}/{status.reason_code}; action authorization cleared")
        self._record_event("OK" if ok else "ERROR", result.message)
        return result

    def recording_status(self) -> dict[str, object]:
        snapshot = self._get_perception_snapshot()
        if snapshot is not None:
            state = getattr(getattr(snapshot, "recording_state", None), "value", None)
            if state:
                self._recording.update({
                    "recording": state == "RECORDING",
                    "recording_state": state,
                    "path": getattr(snapshot, "recording_path", None) or "",
                    "frames": int(getattr(snapshot, "recording_frames", 0)),
                    "error": getattr(snapshot, "recording_error", None),
                    "recorder_boot_id": getattr(snapshot, "recorder_boot_id", None),
                    "recorder_session_id": getattr(snapshot, "recorder_session_id", None),
                    "expires_at_monotonic_ns": getattr(snapshot, "recording_expires_at_monotonic_ns", None),
                    "message": "actual_from_perception",
                })
        return dict(self._recording)

    def recording_start(self, *, trigger: str = "system") -> OperationResult:
        try:
            status = self._yolo_client.start_recording()
        except Exception as exc:
            result = OperationResult(False, f"camera recording command failed: {exc}")
            self._record_event("ERROR", result.message)
            return result
        self._recording.update({"recording": status.recording_state.value == "RECORDING",
            "recording_state": status.recording_state.value, "path": status.actual_path or "",
            "frames": status.frames, "error": status.error, "message": status.reason_code, "trigger": trigger})
        result = OperationResult(status.state.value in {"ACCEPTED", "IN_PROGRESS", "APPLIED"},
                                 f"camera recording start {status.state.value} trigger={trigger}")
        self._record_event("OK", result.message)
        return result

    def recording_stop(self, *, trigger: str = "system") -> OperationResult:
        try:
            status = self._yolo_client.stop_recording()
        except Exception as exc:
            result = OperationResult(False, f"camera recording command failed: {exc}")
            self._record_event("ERROR", result.message)
            return result
        self._recording.update({"recording": status.recording_state.value == "RECORDING",
            "recording_state": status.recording_state.value, "path": status.actual_path or "",
            "frames": status.frames, "error": status.error, "message": status.reason_code, "trigger": trigger})
        result = OperationResult(status.state.value in {"ACCEPTED", "IN_PROGRESS", "APPLIED"},
                                 f"camera recording stop {status.state.value} trigger={trigger}")
        self._record_event("OK", result.message)
        return result

    def recording_toggle(self) -> OperationResult:
        return self.recording_stop(trigger="manual") if self._recording["recording_state"] in {"RECORDING", "START_REQUESTED"} else self.recording_start(trigger="manual")

    def restart_service(self, service: str) -> OperationResult:
        command = list(self._restart_commands.get(service, []))
        if not command:
            return OperationResult(False, f"{service} restart command is not configured")
        self.disable_send(f"{service}_restart")
        self._stop_process(service)
        process = subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._processes[service] = process
        return OperationResult(True, f"{service} restarted; pid={process.pid}; SEND remains OFF")

    def _stop_process(self, service: str) -> None:
        process = self._processes.pop(service, None)
        if process is None: return
        try: os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError): pass
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError): pass

    def stop(self) -> None:
        for service in list(self._processes): self._stop_process(service)
