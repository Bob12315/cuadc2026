from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from contracts.effects import FlightCommand
from execution.authorization import RunAuthorization
from execution.dispatcher import ActionDispatcher
from missions.common.actions.align_descend import AlignDescendAction


class _StatePort:
    def get_active_source(self) -> str:
        return "sitl"

    def get_latest_drone_state(self):
        return SimpleNamespace(connected=True, stale=False, control_allowed=True)


class _CommandPort:
    def __init__(self) -> None:
        self.commands: list[tuple[object, ...]] = []

    def send_velocity_command(self, *args: object, **kwargs: object) -> None:
        self.commands.append((*args, kwargs))


class _BodyCommandPort:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []

    def send_body_velocity(
        self,
        *,
        vx_forward_mps: float,
        vy_right_mps: float,
        vz_down_mps: float,
        yaw_rad: float | None = None,
        yaw_rate_rad_s: float | None = None,
    ) -> None:
        self.commands.append({
            "vx_forward_mps": vx_forward_mps,
            "vy_right_mps": vy_right_mps,
            "vz_down_mps": vz_down_mps,
            "yaw_rad": yaw_rad,
            "yaw_rate_rad_s": yaw_rate_rad_s,
        })


def _authorize(dispatcher: ActionDispatcher, source: str = "sitl") -> None:
    dispatcher.set_authorization(
        RunAuthorization.create(
            operator="test",
            scope_type="action",
            scope_name="align_descend",
            target_source=source,
            allowed_actions={"align_descend"},
        )
    )


def test_production_dispatch_reads_source_from_state_port_only() -> None:
    commands = _CommandPort()
    dispatcher = ActionDispatcher(state_port=_StatePort(), command_port=commands)
    _authorize(dispatcher)
    effect = FlightCommand(params={"valid": True, "vx_cmd": 0.0, "vy_cmd": 0.0, "vz_cmd": 0.0})
    result = dispatcher.dispatch_effects(
        [effect], action_name="align_descend", send_commands=True, link_manager=None
    )
    assert result["accepted"]
    assert commands.commands
    dispatcher.safety_pipeline.stop_continuous("test_cleanup", emit=False)
    dispatcher.safety_pipeline.continuous_guard.close()


def test_missing_state_port_fails_closed_and_never_infers_test_source() -> None:
    commands = _CommandPort()
    dispatcher = ActionDispatcher(command_port=commands)
    _authorize(dispatcher)
    effect = FlightCommand(params={"valid": True, "vx_cmd": 0.0, "vy_cmd": 0.0, "vz_cmd": 0.0})
    result = dispatcher.dispatch_effects(
        [effect], action_name="align_descend", send_commands=True, link_manager=None
    )
    assert result["skipped"][0]["reason"] == "telemetry_state_unavailable"
    assert commands.commands == []


def test_test_source_requires_explicit_fixture_context() -> None:
    dispatcher = ActionDispatcher(test_source="test")
    assert dispatcher._source_for(None) == "test"


def test_align_descend_dispatches_only_body_velocity_with_absolute_yaw_hold() -> None:
    commands = _BodyCommandPort()
    dispatcher = ActionDispatcher(state_port=_StatePort(), command_port=commands)
    _authorize(dispatcher)
    action = AlignDescendAction()
    action.start({"field_yaw_deg": 90.0})
    result = action.update({
        "field_heading_yaw_rad": 0.0,
        "drone": {"relative_altitude": 2.0},
        "scene": {"frame_id": 1, "detections": []},
    })

    dispatch = dispatcher.dispatch_result(
        result,
        action_name="align_descend",
        send_commands=True,
        link_manager=None,
    )

    assert dispatch["accepted"]
    assert commands.commands == [{
        "vx_forward_mps": 0.0,
        "vy_right_mps": 0.0,
        "vz_down_mps": 0.0,
        "yaw_rad": pytest.approx(math.pi / 2),
        "yaw_rate_rad_s": None,
    }]
    dispatcher.safety_pipeline.stop_continuous("test_cleanup", emit=False)
    dispatcher.safety_pipeline.continuous_guard.close()
