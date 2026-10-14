from __future__ import annotations

import logging
import math
import time

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega

try:
    from .config import TelemetryConfig
    from .mavlink_client import MavlinkClient
    from .models import ActionCommand, ActionType, ControlCommand, ControlType, GimbalRateCommand
    from .state_cache import StateCache
except ImportError:  # pragma: no cover - supports direct script execution
    from config import TelemetryConfig
    from mavlink_client import MavlinkClient
    from models import ActionCommand, ActionType, ControlCommand, ControlType, GimbalRateCommand
    from state_cache import StateCache

class MavlinkCommandEncoder:
    """Synchronous wire encoding; CommandBrokerWorker owns command scheduling."""
    def __init__(
        self,
        client: MavlinkClient,
        state_cache: StateCache,
        cfg: TelemetryConfig,
    ) -> None:
        self.client = client
        self.state_cache = state_cache
        self.cfg = cfg
        self.logger = logging.getLogger(self.__class__.__name__)

    def _clamp_gimbal_yaw_deg(self, yaw_deg: float) -> float:
        lower = min(float(self.cfg.gimbal_yaw_min_deg), float(self.cfg.gimbal_yaw_max_deg))
        upper = max(float(self.cfg.gimbal_yaw_min_deg), float(self.cfg.gimbal_yaw_max_deg))
        return max(lower, min(upper, float(yaw_deg)))

    def _clamp_gimbal_pitch_deg(self, pitch_deg: float) -> float:
        lower = min(float(self.cfg.gimbal_pitch_min_deg), float(self.cfg.gimbal_pitch_max_deg))
        upper = max(float(self.cfg.gimbal_pitch_min_deg), float(self.cfg.gimbal_pitch_max_deg))
        return max(lower, min(upper, float(pitch_deg)))

    def _send_control(self, command: ControlCommand) -> None:
        try:
            if command.command_type == ControlType.VELOCITY:
                self.client.send_raw_message(lambda master: self._send_velocity(master, command))
            elif command.command_type == ControlType.YAW_RATE:
                self.client.send_raw_message(lambda master: self._send_yaw_rate(master, command))
            elif command.command_type == ControlType.STOP:
                self.client.send_raw_message(lambda master: self._send_velocity(master, command))
            else:
                return
            self.state_cache.update_link(last_tx_time=time.time())
        except Exception as exc:
            self.logger.warning("failed to send control command: %s", exc)
            raise

    def _send_action(self, command: ActionCommand) -> None:
        try:
            if command.action_type == ActionType.ARM:
                self.logger.info("sending action command=arm")
                self.client.send_raw_message(
                    lambda master: self._command_long(
                        master,
                        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                        [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    )
                )
            elif command.action_type == ActionType.DISARM:
                self.logger.info("sending action command=disarm")
                self.client.send_raw_message(
                    lambda master: self._command_long(
                        master,
                        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    )
                )
            elif command.action_type == ActionType.TAKEOFF:
                altitude_m = float(command.params["altitude_m"])
                self.logger.info("sending action command=takeoff altitude_m=%.2f", altitude_m)
                self.client.send_raw_message(
                    lambda master: self._command_long(
                        master,
                        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, altitude_m],
                    )
                )
            elif command.action_type == ActionType.LAND:
                self.logger.info("sending action command=land")
                self.client.send_raw_message(
                    lambda master: self._command_long(
                        master,
                        mavutil.mavlink.MAV_CMD_NAV_LAND,
                        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    )
                )
            elif command.action_type == ActionType.SET_MODE:
                self.logger.info("sending action command=set_mode mode=%s", str(command.params["mode"]))
                self.client.send_raw_message(lambda master: self._send_set_mode(master, str(command.params["mode"])))
            elif command.action_type == ActionType.REQUEST_MESSAGE_INTERVAL:
                rate_hz = float(command.params["rate_hz"])
                self.logger.info(
                    "sending action command=request_message_interval message=%s rate_hz=%s",
                    str(command.params["message_name"]),
                    "default" if rate_hz <= 0 else f"{rate_hz:.2f}",
                )
                self.client.send_raw_message(
                    lambda master: self._send_message_interval_request(
                        master,
                        message_name=str(command.params["message_name"]),
                        rate_hz=rate_hz,
                    )
                )
            elif command.action_type == ActionType.CONDITION_YAW:
                self.logger.info(
                    "sending action command=condition_yaw yaw_deg=%.2f speed_deg_s=%.2f direction=%s relative=%s",
                    float(command.params["yaw_deg"]),
                    float(command.params["yaw_speed_deg_s"]),
                    int(command.params["direction"]),
                    bool(command.params["relative"]),
                )
                self.client.send_raw_message(
                    lambda master: self._command_long(
                        master,
                        mavutil.mavlink.MAV_CMD_CONDITION_YAW,
                        [
                            float(command.params["yaw_deg"]),
                            float(command.params["yaw_speed_deg_s"]),
                            float(command.params["direction"]),
                            1.0 if bool(command.params["relative"]) else 0.0,
                            0.0,
                            0.0,
                            0.0,
                        ],
                    )
                )
            elif command.action_type == ActionType.CHANGE_SPEED:
                self.logger.info(
                    "sending action command=change_speed speed_mps=%.2f speed_type=%s",
                    float(command.params["speed_mps"]),
                    int(command.params["speed_type"]),
                )
                self.client.send_raw_message(
                    lambda master: self._command_long(
                        master,
                        mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
                        [
                            float(command.params["speed_type"]),
                            float(command.params["speed_mps"]),
                            -1.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                        ],
                    )
                )
            elif command.action_type == ActionType.GLOBAL_GOTO:
                goto_yaw = command.params.get("yaw")
                self.logger.info(
                    "sending action command=global_goto lat=%.7f lon=%.7f alt=%.2f frame=%s yaw=%s",
                    float(command.params["lat"]),
                    float(command.params["lon"]),
                    float(command.params["alt"]),
                    int(command.params["frame"]),
                    "ignore" if goto_yaw is None else f"{float(goto_yaw):.3f}",
                )
                self.client.send_raw_message(
                    lambda master: self._send_global_goto(master, command)
                )
            elif command.action_type == ActionType.LOCAL_POSITION:
                yaw = command.params.get("yaw")
                self.logger.info(
                    "sending action command=local_position x=%.2f y=%.2f z=%.2f frame=%s yaw=%s",
                    float(command.params["x"]),
                    float(command.params["y"]),
                    float(command.params["z"]),
                    int(command.params["frame"]),
                    "ignore" if yaw is None else f"{float(yaw):.3f}",
                )
                self.client.send_raw_message(
                    lambda master: self._send_local_position(master, command)
                )
            elif command.action_type == ActionType.SET_SERVO:
                self.logger.info(
                    "sending action command=set_servo channel=%s pwm=%s",
                    int(command.params["channel"]),
                    int(command.params["pwm"]),
                )
                self.client.send_raw_message(lambda master: self._send_set_servo(master, command))
            elif command.action_type == ActionType.GIMBAL_ANGLE:
                self.logger.info(
                    "sending action command=gimbal_angle pitch=%.2f yaw=%.2f roll=%.2f mount_mode=%s",
                    float(command.params["pitch"]),
                    float(command.params["yaw"]),
                    float(command.params.get("roll", 0.0)),
                    int(command.params.get("mount_mode", self.cfg.gimbal_mount_mode)),
                )
                self.client.send_raw_message(
                    lambda master: self._send_gimbal_angle(
                        master,
                        pitch=float(command.params["pitch"]),
                        yaw=float(command.params["yaw"]),
                        roll=float(command.params.get("roll", 0.0)),
                        mount_mode=int(
                            command.params.get(
                                "mount_mode",
                                self.cfg.gimbal_mount_mode,
                            )
                        ),
                    )
                )
            else:
                return
            self.state_cache.update_link(last_tx_time=time.time())
        except Exception as exc:
            self.logger.warning("failed to send action command %s: %s", command.action_type, exc)
            raise

    def _send_gimbal_rate(self, command: GimbalRateCommand) -> None:
        try:
            yaw_rate_deg_s = math.degrees(float(command.yaw_rate))
            pitch_rate_deg_s = math.degrees(float(command.pitch_rate))
            self.logger.debug(
                "sending gimbal rate command pitch_rate=%.2f deg/s yaw_rate=%.2f deg/s yaw_lock=%s gimbal_device_id=%s",
                pitch_rate_deg_s,
                yaw_rate_deg_s,
                bool(command.yaw_lock),
                int(command.gimbal_device_id),
            )
            self.client.send_raw_message(
                lambda master: self._send_gimbal_manager_pitchyaw_rate(
                    master,
                    pitch_rate_deg_s=pitch_rate_deg_s,
                    yaw_rate_deg_s=yaw_rate_deg_s,
                    yaw_lock=bool(command.yaw_lock),
                    gimbal_device_id=int(command.gimbal_device_id),
                )
            )
            self.state_cache.update_link(last_tx_time=time.time())
        except Exception as exc:
            self.logger.warning("failed to send gimbal rate command: %s", exc)
            raise

    def _send_velocity(self, master, command: ControlCommand) -> None:
        if command.yaw is not None and command.yaw_rate is not None:
            raise ValueError("velocity command cannot specify both yaw and yaw_rate")
        if command.yaw is not None:
            type_mask = self._velocity_with_yaw_type_mask()
        elif command.yaw_rate is not None:
            type_mask = self._velocity_yaw_rate_type_mask()
        else:
            type_mask = self._velocity_only_type_mask()
        self.logger.debug(
            "send velocity setpoint frame=%s vx=%.3f vy=%.3f vz=%.3f yaw=%s yaw_rate=%.3f type_mask=%s",
            command.frame,
            command.vx,
            command.vy,
            command.vz,
            command.yaw,
            command.yaw_rate,
            type_mask,
        )
        master.mav.set_position_target_local_ned_send(
            0,
            master.target_system,
            master.target_component,
            command.frame,
            type_mask,
            0.0,
            0.0,
            0.0,
            command.vx,
            command.vy,
            command.vz,
            0.0,
            0.0,
            0.0,
            0.0 if command.yaw is None else command.yaw,
            0.0 if command.yaw_rate is None else command.yaw_rate,
        )

    def _velocity_yaw_rate_type_mask(self) -> int:
        return (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        )

    def _velocity_only_type_mask(self) -> int:
        return (
            self._velocity_yaw_rate_type_mask()
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )

    def _velocity_with_yaw_type_mask(self) -> int:
        return self._velocity_only_type_mask() & ~mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE

    def _send_yaw_rate(self, master, command: ControlCommand) -> None:
        type_mask = self._velocity_yaw_rate_type_mask()
        master.mav.set_position_target_local_ned_send(
            0,
            master.target_system,
            master.target_component,
            command.frame,
            type_mask,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            command.yaw_rate,
        )

    def _position_only_type_mask(self) -> int:
        return (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )

    def _position_with_yaw_type_mask(self) -> int:
        return self._position_only_type_mask() & ~mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE

    def _send_global_goto(self, master, command: ActionCommand) -> None:
        yaw = command.params.get("yaw")
        type_mask = self._position_only_type_mask() if yaw is None else self._position_with_yaw_type_mask()
        master.mav.set_position_target_global_int_send(
            0,
            master.target_system,
            master.target_component,
            int(command.params["frame"]),
            type_mask,
            int(round(float(command.params["lat"]) * 1e7)),
            int(round(float(command.params["lon"]) * 1e7)),
            float(command.params["alt"]),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0 if yaw is None else float(yaw),
            0.0,
        )

    def _send_local_position(self, master, command: ActionCommand) -> None:
        yaw = command.params.get("yaw")
        master.mav.set_position_target_local_ned_send(
            0,
            master.target_system,
            master.target_component,
            int(command.params["frame"]),
            self._position_only_type_mask() if yaw is None else self._position_with_yaw_type_mask(),
            float(command.params["x"]),
            float(command.params["y"]),
            float(command.params["z"]),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0 if yaw is None else float(yaw),
            0.0,
        )

    def _send_set_servo(self, master, command: ActionCommand) -> None:
        self.logger.info(
            "sending COMMAND_LONG MAV_CMD_DO_SET_SERVO command=183 param1_channel=%s param2_pwm=%s",
            float(command.params["channel"]),
            float(command.params["pwm"]),
        )
        self._command_long(
            master,
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            [
                float(command.params["channel"]),
                float(command.params["pwm"]),
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            ],
        )

    def _send_gimbal_manager_pitchyaw_rate(
        self,
        master,
        *,
        pitch_rate_deg_s: float,
        yaw_rate_deg_s: float,
        yaw_lock: bool,
        gimbal_device_id: int,
    ) -> None:
        flags = mavutil.mavlink.GIMBAL_MANAGER_FLAGS_YAW_LOCK if yaw_lock else 0
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW,
            0,
            float("nan"),
            float("nan"),
            float(pitch_rate_deg_s),
            float(yaw_rate_deg_s),
            float(flags),
            0.0,
            float(gimbal_device_id),
        )

    def _send_set_mode(self, master, mode_name: str) -> None:
        mapping = {str(key).upper(): int(value) for key, value in (master.mode_mapping() or {}).items()}
        mode = str(mode_name).strip().upper()
        if mode not in mapping:
            raise ValueError(f"unknown mode: {mode_name}")
        master.set_mode(mapping[mode])

    def _command_long(self, master, command: int, params: list[float]) -> None:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            int(command),
            0,
            float(params[0]),
            float(params[1]),
            float(params[2]),
            float(params[3]),
            float(params[4]),
            float(params[5]),
            float(params[6]),
        )

    def _send_message_interval_request(self, master, message_name: str, rate_hz: float) -> None:
        message_id = getattr(mavutil.mavlink, f"MAVLINK_MSG_ID_{message_name}", None)
        if message_id is None:
            message_id = getattr(ardupilotmega, f"MAVLINK_MSG_ID_{message_name}", None)
        if message_id is None:
            raise ValueError(f"unknown MAVLink message name: {message_name}")
        interval_us = int(1e6 / rate_hz) if rate_hz > 0 else -1
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            message_id,
            interval_us,
            0,
            0,
            0,
            0,
            0,
        )

    def _send_gimbal_angle(
        self,
        master,
        *,
        pitch: float,
        yaw: float,
        roll: float,
        mount_mode: int,
    ) -> None:
        yaw = self._clamp_gimbal_yaw_deg(yaw)
        pitch = self._clamp_gimbal_pitch_deg(pitch)
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_DO_MOUNT_CONTROL,
            0,
            pitch,
            roll,
            yaw,
            0.0,
            0.0,
            0.0,
            float(mount_mode),
        )
