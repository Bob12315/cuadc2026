from __future__ import annotations

import hashlib
import json

from contracts.core.action import (
    ActionContractRef,
    ActionDefinition,
    ActionRegistration,
    EffectDispatchPolicy,
    SchemaRef,
)
from contracts.core.common import ActionContractFingerprint, ActionDefinitionId
from contracts.core.effects import EffectKind
from contracts.platform.common import SchemaVersion
from missions.common.actions.action_lab import action_definitions

from .action_catalog import ActionRegistrationCatalog
from .codecs import FrozenJsonCodec
from .legacy_action_adapter import LegacyActionModuleAdapter
from .native_one_shot_actions import (
    empty_params_codec,
    land_factory,
    output_codec,
    speed_factory,
    speed_params_codec,
)


_EFFECT_KIND_BY_ACTION_EFFECT = {
    "set_mode": EffectKind.SET_FLIGHT_MODE,
    "arm": EffectKind.ARM,
    "takeoff": EffectKind.TAKEOFF,
    "condition_yaw": EffectKind.CONDITION_YAW,
    "land": EffectKind.LAND,
    "change_speed": EffectKind.CHANGE_SPEED,
    "global_goto": EffectKind.GLOBAL_POSITION_TARGET,
    "flight_command": EffectKind.BODY_VELOCITY_TARGET,
    "set_servo": EffectKind.SET_SERVO,
    "yolo_lock_target": EffectKind.SET_VISION_TARGET,
}


_NATIVE_ONE_SHOT = {
    "land": (land_factory, empty_params_codec),
    "change_speed": (speed_factory, speed_params_codec),
}


def create_production_catalog() -> ActionRegistrationCatalog:
    registrations = []
    for legacy in action_definitions():
        schema_text = json.dumps(legacy.parameter_schema, sort_keys=True, separators=(",", ":"))
        fingerprint = ActionContractFingerprint(hashlib.sha256(
            f"{legacy.name}:v1:{schema_text}".encode("utf-8")
        ).hexdigest())
        ref = ActionContractRef(ActionDefinitionId(legacy.name), "v1", fingerprint)
        effect_kinds = frozenset(
            _EFFECT_KIND_BY_ACTION_EFFECT[effect_type]
            for effect_type in legacy.allowed_effect_types
        )
        definition = ActionDefinition(
            ref,
            legacy.name,
            legacy.label,
            legacy.description,
            SchemaRef(f"uav.action.{legacy.name}.params", SchemaVersion(1, 0)),
            SchemaRef(f"uav.action.{legacy.name}.output", SchemaVersion(1, 0)),
            effect_kinds,
            frozenset(),
            tuple((kind, EffectDispatchPolicy(1000, 5, 1,
                  100 if kind is EffectKind.BODY_VELOCITY_TARGET else None)) for kind in sorted(effect_kinds, key=lambda k: k.value)),
            legacy.exit_barrier,
            "payload_release_v1" if legacy.name == "payload_release" else None,
        )
        native = _NATIVE_ONE_SHOT.get(legacy.name)
        if native is None:
            factory = lambda legacy=legacy: LegacyActionModuleAdapter(legacy.factory, legacy.name)
            params_codec = FrozenJsonCodec(require_object=True)
            result_codec = FrozenJsonCodec()
        else:
            factory, codec_factory = native
            params_codec = codec_factory()
            result_codec = output_codec()
        registrations.append(ActionRegistration(
            definition, factory, params_codec, result_codec,
        ))
    return ActionRegistrationCatalog(tuple(registrations))
