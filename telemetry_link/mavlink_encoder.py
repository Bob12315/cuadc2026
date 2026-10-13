from __future__ import annotations

import math
import time

from contracts.platform.vehicle_commands import (
    Arm, BodyVelocity, ChangeSpeed, ConditionYaw, GimbalAngle, GimbalRate,
    GlobalPositionTarget, Land, LocalPositionTarget, SetMode, SetServo, StopMotion,
    Takeoff, VehicleCommandEnvelope,
)
from .mavlink_command_encoder import MavlinkCommandEncoder
from .frames import BODY_NED, LOCAL_NED
from .models import ActionCommand, ActionType, ControlCommand, ControlType, GimbalRateCommand
from .ack_router import AckRouter, AckSlot
from pymavlink import mavutil


class MavlinkEnvelopeWriter:
    """Typed, exactly-one writer that reuses the characterized MAVLink encoders."""

    def __init__(self, encoder: MavlinkCommandEncoder, *, ack_router: AckRouter | None = None,
                 link_session_id=lambda: "", target_system=lambda: 0,
                 target_component=lambda: 0, local_system=lambda: 0,
                 local_component=lambda: 0, source=lambda: "") -> None:
        self.encoder = encoder
        self.ack_router = ack_router
        self.link_session_id = link_session_id
        self.target_system = target_system
        self.target_component = target_component
        self.local_system = local_system
        self.local_component = local_component
        self.source = source

    def write(self, value: object) -> None:
        payload = getattr(value, "payload", None)
        if isinstance(value, VehicleCommandEnvelope):
            payload = value.payload
            self._register_ack(value)
        elif isinstance(payload, StopMotion) and hasattr(value, "safety_generation"):
            if time.monotonic_ns() >= int(getattr(value, "deadline_monotonic_ns", 0)):
                raise RuntimeError("safety_barrier_deadline_expired")
            if str(getattr(value, "link_session_id", "")) != str(self.link_session_id()):
                raise RuntimeError("safety_barrier_session_mismatch")
            if str(getattr(value, "source", "")) != str(self.source()):
                raise RuntimeError("safety_barrier_source_mismatch")
        if isinstance(payload, SetMode):
            self._action(ActionType.SET_MODE, {"mode": payload.mode})
        elif isinstance(payload, Arm):
            self._action(ActionType.ARM if payload.arm else ActionType.DISARM, {})
        elif isinstance(payload, Takeoff):
            self._action(ActionType.TAKEOFF, {"altitude_m": payload.altitude_m})
        elif isinstance(payload, Land):
            self._action(ActionType.LAND, {})
        elif isinstance(payload, LocalPositionTarget):
            params = {"x": payload.north_m, "y": payload.east_m, "z": payload.down_m,
                      "frame": LOCAL_NED}
            if payload.yaw_rad is not None: params["yaw"] = payload.yaw_rad
            self._action(ActionType.LOCAL_POSITION, params)
        elif isinstance(payload, GlobalPositionTarget):
            params = {"lat": payload.latitude_deg,
                "lon": payload.longitude_deg, "alt": payload.altitude_m,
                "frame": 6}
            if payload.yaw_rad is not None:
                params["yaw"] = payload.yaw_rad
            self._action(ActionType.GLOBAL_GOTO, params)
        elif isinstance(payload, BodyVelocity):
            self.encoder._send_control(ControlCommand(ControlType.VELOCITY,
                vx=payload.forward_mps, vy=payload.right_mps, vz=payload.down_mps,
                yaw=payload.yaw_rad, yaw_rate=payload.yaw_rate_rad_s,
                timestamp=time.time(), frame=BODY_NED))
        elif isinstance(payload, ConditionYaw):
            self._action(ActionType.CONDITION_YAW, {"yaw_deg": payload.yaw_deg,
                "yaw_speed_deg_s": 20.0, "direction": 0, "relative": payload.relative})
        elif isinstance(payload, ChangeSpeed):
            self._action(ActionType.CHANGE_SPEED, {"speed_mps": payload.speed_mps, "speed_type": 1})
        elif isinstance(payload, SetServo):
            self._action(ActionType.SET_SERVO, {"channel": payload.channel, "pwm": payload.pwm})
        elif isinstance(payload, GimbalAngle):
            self._action(ActionType.GIMBAL_ANGLE, {"yaw": math.degrees(payload.yaw_rad),
                "pitch": math.degrees(payload.pitch_rad), "roll": 0.0})
        elif isinstance(payload, GimbalRate):
            self.encoder._send_gimbal_rate(GimbalRateCommand(payload.yaw_rate_rad_s,
                payload.pitch_rate_rad_s, created_at=time.time()))
        elif isinstance(payload, StopMotion):
            self.encoder._send_control(ControlCommand(ControlType.STOP, vx=0.0, vy=0.0,
                vz=0.0, yaw_rate=0.0, timestamp=time.time(), frame=BODY_NED))
        else:
            raise ValueError(f"unsupported typed MAVLink payload: {type(payload).__name__}")

    def mark_transmitted(self, command: VehicleCommandEnvelope) -> None:
        if self.ack_router is not None:
            self.ack_router.mark_transmitted(command.command_id)

    def mark_write_failed(self, command: VehicleCommandEnvelope) -> None:
        if self.ack_router is not None:
            self.ack_router.abort(command.command_id)

    def _register_ack(self, command: VehicleCommandEnvelope) -> None:
        if self.ack_router is None:
            return
        mapping = {
            "arm": mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            "takeoff": mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            "land": mavutil.mavlink.MAV_CMD_NAV_LAND,
            "condition_yaw": mavutil.mavlink.MAV_CMD_CONDITION_YAW,
            "change_speed": mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            "set_servo": mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            "gimbal_angle": mavutil.mavlink.MAV_CMD_DO_MOUNT_CONTROL,
            "gimbal_rate": mavutil.mavlink.MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW,
        }
        mav_command = mapping.get(command.payload.kind)
        if mav_command is None:
            return
        self.ack_router.register(AckSlot(
            command.command_id, command.expected_link_session_id, mav_command,
            self.target_system(), self.target_component(),
            discard=command.ack_policy.value == "DISABLED",
            ack_deadline_monotonic_ns=time.monotonic_ns() + command.ack_timeout_ms * 1_000_000,
            total_deadline_monotonic_ns=command.deadline_monotonic_ns,
            local_system=self.local_system(), local_component=self.local_component(),
        ))

    def _action(self, action_type: ActionType, params: dict[str, object]) -> None:
        self.encoder._send_action(ActionCommand(action_type, params, created_at=time.time()))
