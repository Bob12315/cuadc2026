from __future__ import annotations

from contracts.core.action import ActionStepResult, ActionStepState, ActionTickContext, EffectEmission
from contracts.core.common import FrozenObject, thaw_json
from contracts.core.effects import (
    Arm,
    BodyVelocityTarget,
    ChangeSpeed,
    ConditionYaw,
    GlobalPositionTarget,
    Land,
    LocalPositionTarget,
    SetFlightMode,
    SetServo,
    SetVisionTarget,
    Takeoff,
)


class LegacyActionModuleAdapter:
    """Single, explicit old-algorithm boundary used by the production core catalog."""

    def __init__(self, action_factory: object, action_name: str) -> None:
        if not callable(action_factory):
            raise TypeError("legacy action factory must be callable")
        self.action_factory = action_factory
        self.action_name = action_name
        self._action = action_factory()
        self._emission_sequence = 0

    def start(self, params: object, context: ActionTickContext) -> ActionStepResult:
        decoded = thaw_json(params) if isinstance(params, (FrozenObject, tuple)) else params
        if not isinstance(decoded, dict):
            raise ValueError("legacy Action params must decode to an object")
        self._action.start(decoded)
        return ActionStepResult(ActionStepState.RUNNING)

    def step(self, context: ActionTickContext) -> ActionStepResult:
        result = self._action.update(self._context(context))
        if bool(result.failed):
            return ActionStepResult(ActionStepState.FAILED, output=result.detail, reason_code=result.reason)
        if bool(result.done):
            return ActionStepResult(ActionStepState.SUCCEEDED, output=result.detail, reason_code=result.reason)
        emissions = list(self._effects(result.effects))
        if self.action_name == "align_descend":
            command = result.detail.get("command") if isinstance(result.detail, dict) else None
            if isinstance(command, dict) and bool(command.get("enable_body")):
                emissions.append(self._emission(BodyVelocityTarget(
                    float(command.get("vx_cmd", 0.0)),
                    float(command.get("vy_cmd", 0.0)),
                    float(command.get("vz_cmd", 0.0)),
                    float(command.get("yaw_rate_cmd", 0.0)),
                )))
        return ActionStepResult(ActionStepState.RUNNING, tuple(emissions), reason_code=result.reason)

    def stop(self, context: ActionTickContext) -> None:
        self._action.stop()

    def _effects(self, effects) -> tuple[EffectEmission, ...]:
        output = []
        for effect in effects:
            params = dict(effect.params)
            kind = effect.action_type
            converted = None
            if kind == "set_mode":
                converted = SetFlightMode(str(params.get("mode") or params.get("custom_mode") or "GUIDED"))
            elif kind == "arm":
                converted = Arm()
            elif kind == "takeoff":
                converted = Takeoff(float(params.get("altitude_m", params.get("altitude", 1.0))))
            elif kind == "land":
                converted = Land()
            elif kind == "condition_yaw":
                converted = ConditionYaw(float(params.get("yaw_deg", params.get("heading_deg", 0.0))),
                                         bool(params.get("relative", False)))
            elif kind == "change_speed":
                converted = ChangeSpeed(float(params.get("speed_mps", params.get("speed", 1.0))))
            elif kind == "local_position":
                converted = LocalPositionTarget(
                    float(params.get("north_m", params.get("x", 0.0))),
                    float(params.get("east_m", params.get("y", 0.0))),
                    float(params.get("down_m", params.get("z", 0.0))),
                    None if params.get("yaw_rad") is None else float(params["yaw_rad"]),
                )
            elif kind == "global_goto":
                converted = GlobalPositionTarget(
                    float(params.get("latitude_deg", params.get("lat", 0.0))),
                    float(params.get("longitude_deg", params.get("lon", 0.0))),
                    float(params.get("altitude_m", params.get("alt", 0.0))),
                )
            elif kind == "body_velocity":
                converted = BodyVelocityTarget(
                    float(params.get("forward_mps", params.get("vx_cmd", 0.0))),
                    float(params.get("right_mps", params.get("vy_cmd", 0.0))),
                    float(params.get("down_mps", params.get("vz_cmd", 0.0))),
                    None if params.get("yaw_rate_rad_s") is None else float(params["yaw_rate_rad_s"]),
                )
            elif kind == "set_servo":
                converted = SetServo(int(params.get("channel", params.get("servo", 0))), int(params.get("pwm", 0)))
            elif kind == "yolo_lock_target":
                track_id = params.get("track_id")
                converted = SetVisionTarget(None if track_id is None else int(track_id))
            if converted is not None:
                output.append(self._emission(converted))
        return tuple(output)

    def _emission(self, effect) -> EffectEmission:
        self._emission_sequence += 1
        return EffectEmission(f"legacy:{self.action_name}:{self._emission_sequence}", effect)

    @staticmethod
    def _context(context: ActionTickContext) -> dict[str, object]:
        fusion_payload = thaw_json(context.snapshot.fusion.payload)
        output = dict(fusion_payload) if isinstance(fusion_payload, dict) else {}
        vehicle = context.snapshot.vehicle
        if vehicle is not None:
            output.setdefault("drone", {
                "armed": vehicle.armed,
                "mode": vehicle.mode,
                "attitude_valid": vehicle.yaw_rad is not None and not vehicle.stale,
                "local_position_valid": vehicle.local_valid,
                "local_north_m": vehicle.local_north_m,
                "local_east_m": vehicle.local_east_m,
                "local_down_m": vehicle.local_down_m,
                "yaw": vehicle.yaw_rad,
                "latitude": vehicle.latitude_deg,
                "longitude": vehicle.longitude_deg,
                "relative_altitude": vehicle.relative_altitude_m,
                "relative_altitude_m": vehicle.relative_altitude_m,
            })
        field = context.snapshot.field
        if field is not None:
            output.setdefault("field_heading_confirmed", bool(field.is_confirmed and field.is_frozen))
            output.setdefault("field_heading_yaw_rad", field.field_heading_yaw_rad)
        perception = context.snapshot.perception
        if perception is not None:
            target = perception.target
            output.setdefault("target", {} if target is None else {
                "target_valid": True,
                "track_id": target.track_id,
                "class_name": target.class_name,
                "confidence": target.confidence,
                "cx": target.center_x_px,
                "cy": target.center_y_px,
            })
            output.setdefault("scene", {
                "frame_id": perception.frame_id,
                "image_width": perception.image_width_px,
                "image_height": perception.image_height_px,
                "detections": [
                    {"track_id": item.track_id, "class_id": item.class_id,
                     "class_name": item.class_name, "confidence": item.confidence,
                     "x1": item.x1_px, "y1": item.y1_px, "x2": item.x2_px, "y2": item.y2_px}
                    for item in perception.detections
                ],
            })
        return output
