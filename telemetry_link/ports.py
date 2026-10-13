"""Narrow state/read and command/write adapters around LinkManager."""
from __future__ import annotations

from typing import Callable
from datetime import datetime, timezone

from contracts.platform.common import ClockStamp, SchemaVersion
from contracts.platform.vehicle_state import LinkControlSnapshot, SourceSwitchReceipt, VehicleStateSnapshot


class VehicleStateAdapter:
    def __init__(self, provider: Callable[[], object | None]) -> None:
        self._provider = provider

    def _link(self):
        return self._provider()

    def get_active_source(self) -> str:
        link = self._link()
        return str(link.get_active_source()) if link is not None else "real"

    def get_latest_drone_state(self):
        from .models import DroneState
        snapshot = self.snapshot()
        return DroneState(
            timestamp=snapshot.captured_at.utc.timestamp(), connected=snapshot.connected,
            stale=snapshot.stale, control_allowed=snapshot.control_allowed,
            armed=snapshot.armed, mode=snapshot.mode or "UNKNOWN",
            landed=snapshot.landed, in_air=snapshot.in_air, failsafe=snapshot.failsafe,
            attitude_valid=snapshot.roll_rad is not None,
            local_position_valid=snapshot.local_valid,
            global_position_valid=snapshot.global_valid,
            velocity_valid=snapshot.velocity_north_mps is not None,
            roll=snapshot.roll_rad or 0.0, pitch=snapshot.pitch_rad or 0.0,
            yaw=snapshot.yaw_rad or 0.0, yaw_rate=snapshot.yaw_rate_rad_s,
            local_x=snapshot.local_north_m or 0.0, local_y=snapshot.local_east_m or 0.0,
            local_z=snapshot.local_down_m or 0.0, vx=snapshot.velocity_north_mps or 0.0,
            vy=snapshot.velocity_east_mps or 0.0, vz=snapshot.velocity_down_mps or 0.0,
            lat=snapshot.latitude_deg or 0.0, lon=snapshot.longitude_deg or 0.0,
        )

    def get_latest_gimbal_state(self):
        from .models import GimbalState
        snapshot = self.snapshot()
        return GimbalState(
            timestamp=snapshot.captured_at.utc.timestamp(), gimbal_valid=snapshot.gimbal_valid,
            yaw=snapshot.gimbal_yaw_rad or 0.0, pitch=snapshot.gimbal_pitch_rad or 0.0,
            roll=snapshot.gimbal_roll_rad or 0.0,
        )

    def get_link_status(self):
        from .models import LinkStatus
        snapshot = self.snapshot()
        return LinkStatus(
            connected=snapshot.connected, target_system=snapshot.target_system_id or 0,
            target_component=snapshot.target_component_id or 0,
            status_text="connected" if snapshot.connected and not snapshot.stale else "disconnected",
        )

    def snapshot(self, source: str | None = None):
        link = self._link()
        if link is None:
            return VehicleStateSnapshot(
                schema=SchemaVersion(1, 0), source=source or "real",
                link_session_id="unavailable", sequence=0,
                captured_at=ClockStamp(datetime.now(timezone.utc), __import__("time").monotonic_ns(), "app"),
                connected=False, stale=True, control_allowed=False,
                target_system_id=None, target_component_id=None, last_rx_age_s=None,
                armed=False, mode=None, landed=None, in_air=None, failsafe=None,
                roll_rad=None, pitch_rad=None, yaw_rad=None, yaw_rate_rad_s=None, attitude_age_s=None,
                local_north_m=None, local_east_m=None, local_down_m=None,
                velocity_north_mps=None, velocity_east_mps=None, velocity_down_mps=None,
                local_valid=False, local_age_s=None,
                latitude_deg=None, longitude_deg=None, altitude_msl_m=None, relative_altitude_m=None,
                global_valid=False, global_age_s=None, global_position_received_at_utc_s=None,
                gps_fix_type=None, satellites_visible=None, gps_eph_m=None, gps_epv_m=None, gps_valid=False,
                battery_voltage_v=None, battery_current_a=None, battery_remaining_pct=None, battery_valid=False,
                gimbal_yaw_rad=None, gimbal_pitch_rad=None, gimbal_roll_rad=None,
                gimbal_valid=False, gimbal_age_s=None,
            )
        from .mavlink_state_adapter import MavlinkVehicleStateAdapter
        return MavlinkVehicleStateAdapter(link.get_state_cache, link.get_active_source).snapshot(source)

    def wait_next(self, *, after_session_id: str, after_sequence: int,
                  timeout_s: float, source: str | None = None):
        link = self._link()
        if link is None:
            raise RuntimeError("telemetry_state_unavailable")
        from .mavlink_state_adapter import MavlinkVehicleStateAdapter
        return MavlinkVehicleStateAdapter(link.get_state_cache, link.get_active_source).wait_next(
            after_session_id=after_session_id,
            after_sequence=after_sequence,
            timeout_s=timeout_s,
            source=source,
        )


class LinkControlAdapter:
    def __init__(self, provider: Callable[[], object | None]) -> None:
        self._provider = provider

    def get_active_source(self) -> str:
        link = self._provider()
        return str(link.get_active_source()) if link is not None else "real"

    def switch_active_source(self, source: str) -> bool:
        link = self._provider()
        return bool(link is not None and link.switch_active_source(source))

    def status(self) -> LinkControlSnapshot:
        link = self._provider()
        if link is None:
            return LinkControlSnapshot("real", 0, False, "unavailable")
        return link.get_link_control_snapshot()

    def activate_source(self, source: str, expected_revision: int) -> SourceSwitchReceipt:
        link = self._provider()
        if link is None:
            return SourceSwitchReceipt(False, "real", "real", 0, "telemetry_unavailable")
        return link.activate_source(source, expected_revision)


class VehicleCommandAdapter:
    """Execution-only forwarding surface; no telemetry read methods."""

    def __init__(self, provider: Callable[[], object | None]) -> None:
        self._provider = provider

    def _call(self, name: str, *args, **kwargs):
        link = self._provider()
        if link is None:
            raise RuntimeError("command_backend_unavailable")
        return getattr(link, name)(*args, **kwargs)

    def set_mode(self, mode: str, *, priority: int = 5): return self._call("set_mode", mode, priority=priority)
    def arm(self, *, priority: int = 1): return self._call("arm", priority=priority)
    def takeoff(self, altitude_m: float, *, priority: int = 2): return self._call("takeoff", altitude_m, priority=priority)
    def land(self, *, priority: int = 2): return self._call("land", priority=priority)
    def condition_yaw(self, yaw_deg, yaw_speed_deg_s=20.0, direction=0, relative=False, *, priority=4):
        return self._call("condition_yaw", yaw_deg, yaw_speed_deg_s, direction, relative, priority=priority)
    def change_speed(self, speed_mps, speed_type=1, *, priority=4):
        return self._call("change_speed", speed_mps, speed_type, priority=priority)
    def local_position(self, x, y, z, frame, yaw=None, priority=4):
        return self._call("local_position", x, y, z, frame, yaw=yaw, priority=priority)
    def goto_local_ned(self, *, x_north_m, y_east_m, z_down_m, yaw_rad=None, priority=4):
        return self._call("goto_local_ned", x_north_m, y_east_m, z_down_m, yaw_rad=yaw_rad, priority=priority)
    def global_goto(self, *, lat, lon, alt, frame, priority=4, yaw_rad=None,
                    field_reference_version=None):
        return self._call("global_goto", lat, lon, alt, frame, priority=priority, yaw_rad=yaw_rad,
                          field_reference_version=field_reference_version)
    def send_velocity_command(self, vx, vy, vz, frame=1, yaw_rad=None, yaw_rate_rad_s=None):
        return self._call("send_velocity_command", vx, vy, vz, frame=frame,
                          yaw_rad=yaw_rad, yaw_rate_rad_s=yaw_rate_rad_s)
    def send_body_velocity(self, *, vx_forward_mps, vy_right_mps, vz_down_mps,
                           yaw_rad=None, yaw_rate_rad_s=None):
        return self._call("send_body_velocity", vx_forward_mps, vy_right_mps,
                          vz_down_mps, yaw_rad=yaw_rad, yaw_rate_rad_s=yaw_rate_rad_s)
    def set_servo(self, channel, pwm, *, priority=3):
        return self._call("set_servo", channel, pwm, priority=priority)
    def set_servo_output_pwm(self, *, servo_output, pwm, priority=3):
        return self._call("set_servo_output_pwm", servo_output, pwm, priority=priority)
    def _cleanup(self, name: str):
        """Best-effort cleanup is idempotent when no backend exists.

        Startup, SEND revocation, and shutdown all invoke these methods even in
        telemetry-disabled deployments.  A missing writer means there is
        nothing to drain; it must not turn the safety cleanup itself into a
        mission-start failure.
        """
        link = self._provider()
        return None if link is None else getattr(link, name)()

    def clear_continuous_commands(self): return self._cleanup("clear_continuous_commands")
    def clear_pending_local_position_actions(self): return self._cleanup("clear_pending_local_position_actions")
    def cancel_run_commands(self, run_id: str):
        link = self._provider()
        return None if link is None else link.cancel_run_commands(run_id)
    def stop_body_velocity_and_clear(self): return self._cleanup("stop_body_velocity_and_clear")
    def hold_current_local_position(self): return self._cleanup("hold_current_local_position")
    def cancel_stale_field_commands(self, reason: str = "field_reference_changed"):
        link = self._provider()
        return None if link is None else link.cancel_stale_field_commands(reason)

    def __getattr__(self, name: str):
        if name.startswith("get_") or name in {"switch_active_source", "start", "stop"}:
            raise AttributeError(name)
        link = self._provider()
        if link is None:
            raise AttributeError(name)
        return getattr(link, name)

    def update_write_context(self, *, run_id: str | None, send_enabled: bool) -> None:
        link = self._provider()
        if link is not None:
            link.update_write_context(run_id=run_id, send_enabled=send_enabled)

    def observation_candidates(self):
        link = self._provider()
        return () if link is None else link.command_observation_candidates()

    def update_completion(self, command_id, state, reason_code: str) -> None:
        link = self._provider()
        if link is not None:
            link.update_command_completion(command_id, state, reason_code)

    def status(self, command_id: str):
        link = self._provider()
        if link is None:
            raise KeyError(command_id)
        return link.command_status(command_id)

    def submit(self, envelope):
        link = self._provider()
        if link is None:
            from contracts.platform.vehicle_commands import CommandSubmissionReceipt, SubmissionState
            import uuid
            return CommandSubmissionReceipt(
                envelope.command_id, SubmissionState.REJECTED,
                "command_backend_unavailable", uuid.uuid4().hex,
            )
        return link.submit_vehicle_command(envelope)

    def cancel(self, request):
        link = self._provider()
        if link is None:
            from contracts.platform.vehicle_commands import BarrierDisposition, CancellationReceipt
            import time
            import uuid
            return CancellationReceipt(
                request.schema, request.cancellation_id, (), (),
                (request.run_id or request.command_id or request.execution_lease_id
                 or request.stream_id or str(request.source or "unknown"),),
                None,
                BarrierDisposition.STOP_UNDELIVERABLE if request.emit_stop_barrier
                else BarrierDisposition.NOT_REQUIRED,
                request.source, None, time.monotonic_ns(), "command_backend_unavailable",
                uuid.uuid4().hex,
            )
        return link.cancel_vehicle_commands(request)
