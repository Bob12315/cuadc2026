from __future__ import annotations

import inspect
import logging
import math
import time
from execution.policy import ACTION_DISPATCH_POLICY, DispatchRule, SafetyGate
from execution.types import empty_dispatch
from execution.normalizer import get_action_params, optional_float, format_log_float
from execution.handlers.servo import dispatch_set_servo
from execution.handlers.global_position import dispatch_global_goto
from execution.handlers.flight_mode import (
    dispatch_set_mode, dispatch_arm, dispatch_takeoff, dispatch_land,
    dispatch_condition_yaw, dispatch_change_speed,
)
from execution.authorization import RunAuthorization
from execution.safety_pipeline import ActionSafetyPipeline, SafetyDecision
from contracts.frames import BODY_NED, LOCAL_NED
from contracts.action import ActionResult
from contracts.effects import Effect, FlightCommand
from execution.handlers.submission import submission_outcome


class ActionDispatcher:
    """Owns Action Lab dispatch logic previously scattered inside SystemRunner.

    Public API mirrors the old SystemRunner methods so the compat wrappers
    are trivial one-liner delegations.
    """

    def __init__(
        self,
        *,
        policy: dict[str, DispatchRule] | None = None,
        logger: logging.Logger | None = None,
        yolo_client: object | None = None,
        safety_pipeline: ActionSafetyPipeline | None = None,
        state_port: object | None = None,
        command_port: object | None = None,
        test_source: str | None = None,
    ) -> None:
        self._policy = policy or ACTION_DISPATCH_POLICY
        self._logger = logger or logging.getLogger(self.__class__.__name__)
        self.yolo_client = yolo_client
        self.state_port = state_port
        self.command_port = command_port
        self.test_source = test_source
        self.authorization: RunAuthorization | None = None
        self.dispatched_keys: set[str] = set()
        self.last_dispatch: dict[str, list[dict[str, object]]] = self.empty_dispatch()
        self.last_servo_command: dict[str, object] | None = None
        self.safety_decisions: list[dict[str, object]] = []
        self.safety_pipeline = safety_pipeline or ActionSafetyPipeline(
            on_decision=self._record_safety_decision,
            allow_test_source=test_source == "test",
        )

    # ------------------------------------------------------------------
    # gate — fully policy-driven (PR A)
    # ------------------------------------------------------------------

    @staticmethod
    def _compat_note_for_action_type(action_type: str) -> str:
        if action_type == "set_servo":
            return "payload_set_servo_dispatch_enabled"
        if action_type in ("flight_command", "body_velocity"):
            return "action_dispatch_enabled"
        return "action_dispatch_enabled"

    def gate(
        self,
        *,
        send_commands: bool,
        action_type: str | None = None,
        action_name: str | None = None,
        source: str | None = None,
    ) -> tuple[bool, str]:
        # When called with action_type, use the policy rule for that type.
        if action_type is not None:
            rule = self._policy.get(action_type)
            if rule is None:
                return False, "unsupported_action_type"
            if action_name is not None and action_name not in rule.allowed_actions:
                return False, "action_dispatch_not_enabled"
            ok, note = SafetyGate.check(
                run_authorized=bool(
                    self.authorization
                    and self.authorization.permits(action_name, source)
                ),
                send_commands=send_commands,
                requires_run_authorization=rule.requires_run_authorization,
                requires_send_commands=rule.requires_send_commands,
            )
            if not ok:
                return ok, note
            return True, self._compat_note_for_action_type(action_type)

        # Without action_type, find *any* rule that allows this action_name.
        if action_name is not None:
            for atype, rule in self._policy.items():
                if action_name in rule.allowed_actions:
                    ok, note = SafetyGate.check(
                        run_authorized=bool(
                            self.authorization
                            and self.authorization.permits(action_name, source)
                        ),
                        send_commands=send_commands,
                        requires_run_authorization=rule.requires_run_authorization,
                        requires_send_commands=rule.requires_send_commands,
                    )
                    if not ok:
                        return ok, note
                    return True, self._compat_note_for_action_type(atype)
        return False, "action_dispatch_not_enabled"

    # ------------------------------------------------------------------
    # dispatch_result
    # ------------------------------------------------------------------

    def dispatch_result(
        self,
        result: ActionResult,
        *,
        action_name: str | None,
        send_commands: bool,
        link_manager: object | None,
    ) -> dict[str, list[dict[str, object]]]:
        link_manager = link_manager or self.command_port
        effects = list(result.effects)
        detail = result.detail
        if isinstance(detail, dict):
            command = detail.get("command")
            if isinstance(command, dict) and command.get("type") == "flight_command":
                self._logger.info(
                    (
                        "align_descend command generated flight_command "
                        "vx=%.3f vy=%.3f vz=%.3f yaw_rate=%.3f active=%s valid=%s "
                        "hold_reason=%s aligned=%s slow_descending=%s ex_cam=%s ey_cam=%s height_m=%s "
                        "finish_altitude_m=%s min_altitude_m=%s yaw_hold_rad=%s"
                    ),
                    float(command.get("vx_cmd", 0.0)),
                    float(command.get("vy_cmd", 0.0)),
                    float(command.get("vz_cmd", 0.0)),
                    float(command.get("yaw_rate_cmd", 0.0)),
                    bool(command.get("active", False)),
                    bool(command.get("valid", False)),
                    str(detail.get("hold_reason", "")),
                    bool(detail.get("aligned", False)),
                    bool(detail.get("slow_descending", False)),
                    self._format_log_float(detail.get("ex_cam")),
                    self._format_log_float(detail.get("ey_cam")),
                    self._format_log_float(detail.get("height_m")),
                    self._format_log_float(detail.get("finish_altitude_m")),
                    self._format_log_float(detail.get("min_altitude_m")),
                    self._format_log_float(command.get("yaw_hold_rad", detail.get("yaw_hold_rad"))),
                )
                effects.append(
                    FlightCommand(
                        params=command,
                        key=f"{action_name or 'action_lab'}_flight_command",
                        once=False,
                        priority=int(command.get("priority", 5)),
                    )
                )
        return self.dispatch_effects(
            effects,
            action_name=action_name,
            send_commands=send_commands,
            link_manager=link_manager,
        )

    def dispatch_effects(
        self,
        effects: list[Effect],
        *, action_name: str | None, send_commands: bool, link_manager: object | None,
    ) -> dict[str, list[dict[str, object]]]:
        link_manager = link_manager or self.command_port
        return self._dispatch_typed_effects(
            effects,
            action_name=action_name,
            send_commands=send_commands,
            link_manager=link_manager,
        )

    @staticmethod
    def _format_log_float(value: object) -> str:
        return format_log_float(value)

    def _dispatch_typed_effects(
        self,
        effects: list[Effect],
        *,
        action_name: str | None,
        send_commands: bool,
        link_manager: object | None,
    ) -> dict[str, list[dict[str, object]]]:
        dispatch = self.empty_dispatch()
        source = self._source_for(self.state_port)
        update_write_context = getattr(link_manager, "update_write_context", None)
        if callable(update_write_context):
            update_write_context(
                run_id=self.authorization.run_id if self.authorization is not None else None,
                send_enabled=bool(send_commands and self.authorization is not None),
            )
        effective, note = self.gate(
            send_commands=send_commands,
            action_name=action_name,
            source=source,
        )
        self._logger.info(
            "action dispatch gate current_action=%s run_id=%s source=%s send_commands=%s effective=%s note=%s",
            action_name,
            self.authorization.run_id if self.authorization else None,
            source,
            bool(send_commands),
            bool(effective),
            note,
        )
        if not effects:
            return dispatch
        if source == "unavailable":
            for effect in effects:
                dispatch["skipped"].append(
                    {
                        "action": effect.to_request(),
                        "action_type": effect.action_type,
                        "reason": "telemetry_state_unavailable",
                    }
                )
            return dispatch

        for effect in effects:
            action_type = effect.action_type
            original_action = effect.to_request()
            action_allowed, action_note = self.gate(
                send_commands=send_commands,
                action_type=action_type,
                action_name=action_name,
                source=source,
            )
            self._logger.info(
                "action_lab dispatch decision current_action=%s action_type=%s dispatch_allowed=%s note=%s",
                action_name,
                action_type,
                bool(action_allowed),
                action_note,
            )
            if not action_allowed:
                dispatch["skipped"].append(
                    {"action": original_action, "action_type": action_type, "reason": action_note}
                )
                continue
            request = dict(original_action)
            request.setdefault("generated_at_monotonic", time.monotonic())
            decision = self.safety_pipeline.evaluate(
                request,
                action_name=action_name,
                source=source,
                authorization=self.authorization,
                state_port=self.state_port,
            )
            if decision.status == "rejected" or decision.effective_request is None:
                dispatch["skipped"].append(
                    {
                        "action": original_action,
                        "action_type": action_type,
                        "reason": decision.reason_code,
                        "safety_decision": decision.to_dict(),
                    }
                )
                continue
            action = decision.effective_request
            key = str(action.get("key") or "")
            rule = self._policy.get(action_type)
            once_respected = rule.once_respected if rule is not None else True
            once_enabled = bool(action.get("once", False)) and once_respected
            if once_enabled and key and key in self.dispatched_keys:
                dispatch["skipped"].append(
                    {"action": action, "action_type": action_type, "reason": "once_already_dispatched"}
                )
                continue
            if action_type == "set_servo":
                assert self.authorization is not None
                if not self.safety_pipeline.mark_servo_sent(self.authorization.run_id, key):
                    dispatch["skipped"].append(
                        {"action": action, "action_type": action_type, "reason": "servo_duplicate_key"}
                    )
                    continue
            if action_type in {"global_goto", "land", "takeoff"}:
                self.safety_pipeline.stop_continuous("transition_to_discrete_control")
            elif action_type == "clear_continuous_commands":
                self.safety_pipeline.stop_continuous("explicit_continuous_clear", emit=False)
            try:
                outcome = self._dispatch_action(action, link_manager=link_manager)
            except Exception as exc:
                self._logger.exception("action lab dispatch failed")
                dispatch["errors"].append({"action": action, "action_type": action_type, "error": str(exc)})
                if isinstance(action, dict) and action.get("action_type") == "set_servo":
                    self.last_servo_command = {
                        "channel": (action.get("params") or {}).get("channel")
                        if isinstance(action.get("params"), dict)
                        else None,
                        "pwm": (action.get("params") or {}).get("pwm")
                        if isinstance(action.get("params"), dict)
                        else None,
                        "priority": action.get("priority", 3),
                        "time": time.time(),
                        "key": str(action.get("key") or ""),
                        "ack": None,
                        "error": str(exc),
                    }
                continue
            if outcome["status"] == "accepted":
                sent = {
                    "action": action,
                    "safety_decision": decision.to_dict(),
                    **dict(outcome.get("detail") or {}),
                }
                dispatch[outcome["status"]].append(sent)
                self._logger.info("action_lab dispatch accepted action_type=%s detail=%s", action_type, sent)
            elif outcome["status"] == "skipped":
                skipped = {
                    "action": action,
                    "action_type": action_type,
                    "reason": str(outcome["reason"]),
                    **dict(outcome.get("detail") or {}),
                }
                dispatch["skipped"].append(skipped)
                self._logger.info("action_lab dispatch skipped action_type=%s reason=%s", action_type, outcome["reason"])
                continue
            else:
                dispatch["errors"].append(
                    {"action": action, "action_type": action_type, "error": str(outcome["reason"])}
                )
                self._logger.info("action_lab dispatch error action_type=%s reason=%s", action_type, outcome["reason"])
                continue
            if once_enabled and key:
                self.dispatched_keys.add(key)
            if (
                outcome["status"] == "accepted"
                and action_type in {"flight_command", "body_velocity"}
                and self.authorization is not None
                and action_name is not None
                and link_manager is not None
            ):
                self.safety_pipeline.arm_continuous(
                    request=action,
                    action_name=action_name,
                    source=source,
                    authorization=self.authorization,
                    state_port=self.state_port,
                    command_port=link_manager,
                )
        return dispatch

    # ------------------------------------------------------------------
    # _dispatch_action — mirrors _dispatch_action_lab_action
    # ------------------------------------------------------------------

    def _dispatch_action(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        action_type = str(action.get("action_type") or "")
        if action_type == "set_servo":
            return self._dispatch_set_servo(action, link_manager=link_manager)
        if action_type == "set_mode":
            return self._dispatch_set_mode(action, link_manager=link_manager)
        if action_type == "arm":
            return self._dispatch_arm(action, link_manager=link_manager)
        if action_type == "takeoff":
            return self._dispatch_takeoff(action, link_manager=link_manager)
        if action_type == "land":
            return self._dispatch_land(action, link_manager=link_manager)
        if action_type == "condition_yaw":
            return self._dispatch_condition_yaw(action, link_manager=link_manager)
        if action_type == "change_speed":
            return self._dispatch_change_speed(action, link_manager=link_manager)
        if action_type == "global_goto":
            return self._dispatch_global_goto(action, link_manager=link_manager)
        if action_type in ("flight_command", "body_velocity"):
            return self._dispatch_flight_command(action, link_manager=link_manager)
        if action_type == "yolo_lock_target":
            return self._dispatch_yolo_lock_target(action, link_manager=link_manager)
        if action_type == "clear_continuous_commands":
            return self._dispatch_clear_continuous_commands(action, link_manager=link_manager)
        return {"status": "skipped", "reason": "unsupported_action_type"}

    # ------------------------------------------------------------------
    # per-type dispatchers
    # ------------------------------------------------------------------

    @staticmethod
    def _action_params(action: dict[str, object]) -> dict[str, object]:
        return get_action_params(action)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        return optional_float(value)

    def _dispatch_set_servo(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        result, sc = dispatch_set_servo(action, link_manager=link_manager)
        if sc is not None:
            self.last_servo_command = sc
        if result.get("status") == "accepted":
            detail = result.get("detail", {})
            self._logger.info(
                "action_lab dispatch set_servo channel=%s pwm=%s priority=%s key=%s",
                detail.get("channel"),
                detail.get("pwm"),
                action.get("priority", 3),
                action.get("key"),
            )
        return result

    def _dispatch_set_mode(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        return dispatch_set_mode(action, link_manager=link_manager)

    def _dispatch_arm(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        return dispatch_arm(action, link_manager=link_manager)

    def _dispatch_takeoff(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        return dispatch_takeoff(action, link_manager=link_manager)

    def _dispatch_land(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        return dispatch_land(action, link_manager=link_manager)

    def _dispatch_global_goto(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        result, log_msg = dispatch_global_goto(action, link_manager=link_manager)
        if log_msg is not None:
            self._logger.info(log_msg)
        return result

    def _dispatch_condition_yaw(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        result = dispatch_condition_yaw(action, link_manager=link_manager)
        detail = result.get("detail", {})
        if result.get("status") == "accepted" and isinstance(detail, dict):
            self._logger.info(
                "action_lab dispatch condition_yaw yaw_deg=%s speed_deg_s=%s direction=%s relative=%s priority=%s key=%s",
                detail.get("yaw_deg"),
                detail.get("yaw_speed_deg_s"),
                detail.get("direction"),
                detail.get("relative"),
                detail.get("priority"),
                detail.get("key"),
            )
        return result

    def _dispatch_change_speed(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        result = dispatch_change_speed(action, link_manager=link_manager)
        detail = result.get("detail", {})
        if result.get("status") == "accepted" and isinstance(detail, dict):
            self._logger.info(
                "action_lab dispatch change_speed speed_mps=%s speed_type=%s priority=%s key=%s",
                detail.get("speed_mps"), detail.get("speed_type"),
                detail.get("priority"), detail.get("key"),
            )
        return result

    def _dispatch_flight_command(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        command = self._action_params(action)
        valid = bool(command.get("valid", False))
        active = bool(command.get("active", False))
        vx = float(command.get("vx_body_mps", command.get("vx_cmd", 0.0)))
        vy = float(command.get("vy_body_mps", command.get("vy_cmd", 0.0)))
        vz = float(command.get("vz_body_mps", command.get("vz_cmd", 0.0)))
        yaw_rate = float(command.get("yaw_rate_cmd", 0.0))
        yaw_rate_rad_s = self._optional_float(command.get("yaw_rate_rad_s"))
        yaw_hold_rad = self._optional_float(command.get("yaw_hold_rad"))
        velocity_yaw_rad = self._optional_float(command.get("velocity_yaw_rad"))
        priority = int(action.get("priority", command.get("priority", 5)))
        send_vx = vx if active else 0.0
        send_vy = vy if active else 0.0
        send_vz = vz if active else 0.0
        send_yaw_rate = yaw_rate if active else 0.0
        detail_action_type = str(action.get("action_type") or "flight_command")
        detail = {
            "action_type": detail_action_type,
            "vx_cmd": send_vx,
            "vy_cmd": send_vy,
            "vz_cmd": send_vz,
            "yaw_rate_cmd": send_yaw_rate,
            "priority": priority,
            "key": str(action.get("key") or ""),
            "active": active,
            "valid": valid,
            "enable_body": bool(command.get("enable_body", False)),
            "enable_approach": bool(command.get("enable_approach", False)),
        }
        if yaw_hold_rad is not None:
            detail["yaw_hold_rad"] = yaw_hold_rad
            if velocity_yaw_rad is None:
                velocity_yaw_rad = yaw_hold_rad
            detail["velocity_yaw_rad"] = velocity_yaw_rad
        if yaw_rate_rad_s is not None:
            detail["yaw"] = "ignored"
            detail["yaw_rate_rad_s"] = yaw_rate_rad_s
            detail["yaw_rate_valid"] = True
        if not valid:
            return {
                "status": "skipped",
                "reason": "flight_command_inactive",
                "detail": detail,
            }

        frame = BODY_NED
        if yaw_rate_rad_s is not None:
            if yaw_hold_rad is not None:
                return {
                    "status": "skipped",
                    "reason": "flight_command_yaw_and_yaw_rate_conflict",
                    "detail": detail,
                }
            wrapper = getattr(link_manager, "send_body_velocity", None)
            if callable(wrapper):
                self._logger.info(
                    "action_lab dispatch body_velocity vx_forward_mps=%.3f vy_right_mps=%.3f vz_down_mps=%.3f frame=BODY_NED yaw=ignored yaw_rate_rad_s=%.3f yaw_rate_valid=true key=%s active=%s",
                    send_vx, send_vy, send_vz,
                    yaw_rate_rad_s,
                    action.get("key"),
                    active,
                )
                kwargs: dict[str, object] = {
                    "vx_forward_mps": send_vx,
                    "vy_right_mps": send_vy,
                    "vz_down_mps": send_vz,
                    "yaw_rate_rad_s": yaw_rate_rad_s,
                }
                receipt = wrapper(**kwargs)
            else:
                sender = getattr(link_manager, "send_velocity_command", None) if link_manager is not None else None
                if not callable(sender):
                    return {
                        "status": "skipped",
                        "reason": "flight_command_dispatch_not_available",
                        "detail": detail,
                    }
                if not self._callable_accepts_keyword(sender, "yaw_rate_rad_s"):
                    return {
                        "status": "skipped",
                        "reason": "flight_command_yaw_rate_dispatch_not_available",
                        "detail": detail,
                    }
                receipt = sender(send_vx, send_vy, send_vz, frame=frame, yaw_rate_rad_s=yaw_rate_rad_s)
            detail["frame"] = frame
            return submission_outcome(receipt, detail)
        if yaw_hold_rad is not None:
            body_wrapper = getattr(link_manager, "send_body_velocity", None)
            if callable(body_wrapper) and self._callable_accepts_keyword(body_wrapper, "yaw_rad"):
                receipt = body_wrapper(
                    vx_forward_mps=send_vx,
                    vy_right_mps=send_vy,
                    vz_down_mps=send_vz,
                    yaw_rad=yaw_hold_rad,
                )
                detail["frame"] = BODY_NED
                detail["velocity_yaw_rad"] = yaw_hold_rad
                return submission_outcome(receipt, detail)
            sender = getattr(link_manager, "send_velocity_command", None) if link_manager is not None else None
            if not callable(sender):
                return {
                    "status": "skipped",
                    "reason": "flight_command_dispatch_not_available",
                    "detail": detail,
                }
            local_vx, local_vy = self._body_velocity_to_local_ned(
                vx_forward_mps=send_vx,
                vy_right_mps=send_vy,
                yaw_rad=velocity_yaw_rad,
            )
            self._logger.info(
                (
                    "action_lab dispatch yaw_hold_velocity local_vx_north_mps=%.3f "
                    "local_vy_east_mps=%.3f vz_down_mps=%.3f velocity_yaw_rad=%s yaw_hold_rad=%s "
                    "frame=LOCAL_NED priority=%s key=%s active=%s"
                ),
                local_vx,
                local_vy,
                send_vz,
                self._format_log_float(velocity_yaw_rad),
                self._format_log_float(yaw_hold_rad),
                priority,
                action.get("key"),
                active,
            )
            if self._callable_accepts_keyword(sender, "yaw_rad"):
                receipt = sender(local_vx, local_vy, send_vz, frame=LOCAL_NED, yaw_rad=yaw_hold_rad)
            else:
                receipt = sender(local_vx, local_vy, send_vz, frame=LOCAL_NED)
            detail["frame"] = LOCAL_NED
            detail["vx_local_ned"] = local_vx
            detail["vy_local_ned"] = local_vy
            return submission_outcome(receipt, detail)

        # prefer semantic wrapper (T4)
        wrapper = getattr(link_manager, "send_body_velocity", None)
        if callable(wrapper):
            yaw_supported = self._callable_accepts_keyword(wrapper, "yaw_rad")
            self._logger.info(
                (
                    "action_lab dispatch send_body_velocity vx_forward_mps=%.3f "
                    "vy_right_mps=%.3f vz_down_mps=%.3f yaw_hold_rad=%s key=%s active=%s"
                ),
                send_vx,
                send_vy,
                send_vz,
                self._format_log_float(yaw_hold_rad),
                action.get("key"),
                active,
            )
            kwargs: dict[str, object] = {
                "vx_forward_mps": send_vx,
                "vy_right_mps": send_vy,
                "vz_down_mps": send_vz,
            }
            if yaw_hold_rad is not None and yaw_supported:
                kwargs["yaw_rad"] = yaw_hold_rad
            receipt = wrapper(**kwargs)
            detail["frame"] = frame
            return submission_outcome(receipt, detail)

        # fallback: original send_velocity_command
        sender = getattr(link_manager, "send_velocity_command", None) if link_manager is not None else None
        if not callable(sender):
            return {
                "status": "skipped",
                "reason": "flight_command_dispatch_not_available",
                "detail": detail,
            }
        self._logger.info(
            (
                "action_lab dispatch flight_command vx=%.3f vy=%.3f vz=%.3f "
                "yaw_rate=%.3f yaw_hold_rad=%s frame=BODY_NED priority=%s key=%s active=%s"
            ),
            send_vx,
            send_vy,
            send_vz,
            send_yaw_rate,
            self._format_log_float(yaw_hold_rad),
            priority,
            action.get("key"),
            active,
        )
        if yaw_hold_rad is not None and self._callable_accepts_keyword(sender, "yaw_rad"):
            receipt = sender(send_vx, send_vy, send_vz, frame=frame, yaw_rad=yaw_hold_rad)
        else:
            receipt = sender(send_vx, send_vy, send_vz, frame=frame)
        detail["frame"] = frame
        return submission_outcome(receipt, detail)

    @staticmethod
    def _body_velocity_to_local_ned(
        *,
        vx_forward_mps: float,
        vy_right_mps: float,
        yaw_rad: float,
    ) -> tuple[float, float]:
        cos_yaw = math.cos(yaw_rad)
        sin_yaw = math.sin(yaw_rad)
        vx_north = vx_forward_mps * cos_yaw - vy_right_mps * sin_yaw
        vy_east = vx_forward_mps * sin_yaw + vy_right_mps * cos_yaw
        return vx_north, vy_east

    def _dispatch_yolo_lock_target(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        params = self._action_params(action)
        track_id = int(params["track_id"])
        detail: dict[str, object] = {
            "action_type": "yolo_lock_target",
            "track_id": track_id,
            "key": str(action.get("key") or ""),
        }
        if self.yolo_client is None:
            return {"status": "skipped", "reason": "yolo_client_not_available", "detail": detail}
        try:
            lock_target = getattr(self.yolo_client, "lock_target", None)
            if not callable(lock_target):
                return {"status": "skipped", "reason": "yolo_client_not_callable", "detail": detail}
            status = lock_target(track_id)
        except Exception as exc:
            self._logger.exception("yolo_lock_target dispatch failed")
            return {"status": "error", "reason": str(exc), "detail": detail}
        state = str(getattr(getattr(status, "state", None), "value", ""))
        reason = str(getattr(status, "reason_code", "vision_status_unavailable"))
        detail.update({"command_id": getattr(status, "command_id", None), "vision_state": state})
        if state in {"ACCEPTED", "IN_PROGRESS", "APPLIED"}:
            return {"status": "accepted", "reason": reason, "detail": detail}
        return {"status": "skipped", "reason": reason, "detail": detail}

    def _dispatch_clear_continuous_commands(
        self,
        action: dict[str, object],
        *,
        link_manager: object | None,
    ) -> dict[str, object]:
        if link_manager is None:
            return {"status": "skipped", "reason": "link_manager_not_available"}

        params = self._action_params(action)
        send_stop_first = bool(params.get("send_stop_first", False))
        clear_pending_local_position = bool(params.get("clear_pending_local_position", False))

        if send_stop_first:
            stop_and_clear = getattr(link_manager, "stop_body_velocity_and_clear", None)
            if not callable(stop_and_clear):
                return {
                    "status": "skipped",
                    "reason": "stop_body_velocity_and_clear_not_available",
                }
            cancellation = stop_and_clear()
        else:
            clear_continuous = getattr(link_manager, "clear_continuous_commands", None)
            if not callable(clear_continuous):
                return {
                    "status": "skipped",
                    "reason": "clear_continuous_commands_not_available",
                }
            cancellation = clear_continuous()

        clear_nav = getattr(link_manager, "clear_pending_local_position_actions", None)
        if clear_pending_local_position and callable(clear_nav):
            clear_nav()

        return {
            "status": "accepted",
            "detail": {
                "action_type": "clear_continuous_commands",
                "clear_pending_local_position": clear_pending_local_position,
                "send_stop_first": send_stop_first,
                "cancellation_id": getattr(cancellation, "cancellation_id", None),
                "barrier_disposition": getattr(
                    getattr(cancellation, "barrier_disposition", None), "value", None
                ),
            },
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _callable_accepts_keyword(func, name: str) -> bool:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return True
        return any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or parameter.name == name
            for parameter in signature.parameters.values()
        )

    # ------------------------------------------------------------------
    # payload — builds the action_lab status payload dict
    # ------------------------------------------------------------------

    def payload(
        self,
        *,
        status: dict[str, object],
        action_name: str | None,
        send_commands: bool,
    ) -> dict[str, object]:
        source = self.authorization.target_source if self.authorization else None
        effective, note = self.gate(
            send_commands=send_commands,
            action_name=action_name,
            source=source,
        )
        return {
            "run_authorized": self.authorization is not None,
            "run_id": self.authorization.run_id if self.authorization else None,
            "run_authorization": self.authorization.to_dict() if self.authorization else None,
            "dispatch_effective": bool(effective),
            "note": note,
            "dispatch": dict(self.last_dispatch),
            "safety_decisions": list(self.safety_decisions[-50:]),
            "last_servo_command": self.last_servo_command,
            "status": status,
        }

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def empty_dispatch() -> dict[str, list[dict[str, object]]]:
        return empty_dispatch()

    def reset_keys(self) -> None:
        self.dispatched_keys.clear()
        self.last_dispatch = self.empty_dispatch()
        self.last_servo_command = None

    def release_action_keys(self) -> None:
        """Release deduplication keys when an Action reaches any terminal state.

        Keep ``last_dispatch`` intact so Action Lab and the cycle log retain
        the final result while the active-action registry is already clean.
        """
        self.dispatched_keys.clear()

    def set_authorization(self, authorization: RunAuthorization | None) -> None:
        previous = self.authorization
        if previous is not None and (
            authorization is None or authorization.run_id != previous.run_id
        ):
            self.safety_pipeline.stop_continuous("run_authorization_revoked")
            self.safety_pipeline.reset_run(previous.run_id)
        self.authorization = authorization
        self.reset_keys()

    def clear_authorization(self, reason: str = "run_authorization_revoked") -> SafetyDecision | None:
        previous = self.authorization
        decision = self.safety_pipeline.stop_continuous(reason)
        if previous is not None:
            self.safety_pipeline.reset_run(previous.run_id)
        self.authorization = None
        return decision

    def _source_for(self, state_port: object | None) -> str:
        getter = getattr(state_port, "get_active_source", None)
        if callable(getter):
            try:
                source = str(getter())
            except Exception:
                return "unavailable"
            return source if source else "unavailable"
        return self.test_source or "unavailable"

    def _record_safety_decision(self, decision: SafetyDecision) -> None:
        payload = decision.to_dict()
        self.safety_decisions.append(payload)
        if len(self.safety_decisions) > 200:
            del self.safety_decisions[:-200]
        self._logger.info(
            "action safety decision status=%s reason=%s run_id=%s action=%s source=%s",
            decision.status,
            decision.reason_code,
            decision.run_id,
            decision.action,
            decision.source,
        )
