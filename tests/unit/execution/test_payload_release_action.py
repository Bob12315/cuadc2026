from __future__ import annotations

import json

import pytest

from execution.dispatcher import ActionDispatcher
from missions.common.actions import payload_release as payload_release_module
from missions.common.actions.payload_release import PayloadReleaseAction


def _params(**overrides):
    data = {
        "servo_outputs": [
            {"channel": 8, "release_pwm": 1200, "hold_pwm": 1700},
        ],
        "payload_id": "payload_1",
        "target_id": "target_a",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "params",
    [
        _params(servo_outputs=[{"channel": 8, "hold_pwm": 1700}]),
        _params(servo_outputs=[{"channel": 8, "release_pwm": 1200}]),
        _params(servo_outputs=[{"channel": 8, "release_pwm": 499, "hold_pwm": 1700}]),
        _params(servo_outputs=[{"channel": 8, "release_pwm": 2501, "hold_pwm": 1700}]),
        _params(servo_outputs=[{"channel": 8, "release_pwm": 1200, "hold_pwm": 499}]),
        _params(servo_outputs=[{"channel": 8, "release_pwm": 1200, "hold_pwm": 2501}]),
        _params(servo_outputs=[]),
        _params(servo_outputs="8"),
        _params(servo_outputs=[{"channel": 0, "release_pwm": 1200, "hold_pwm": 1700}]),
        _params(servo_outputs=None, servo_channels=[0]),
        _params(servo_outputs=None, servo_channels="8"),
        _params(servo_outputs=None, servo_channels=None, channels=[]),
        _params(servo_outputs=None, servo_channels=[8], release_pwm=None),
        _params(servo_outputs=None, servo_channels=[8], hold_pwm=None),
        _params(payload_id=""),
        _params(target_id=""),
        _params(release_wait_updates=0),
    ],
)
def test_start_rejects_invalid_params(params) -> None:
    action = PayloadReleaseAction()

    with pytest.raises(ValueError):
        action.start(params)


def test_update_before_start_fails() -> None:
    result = PayloadReleaseAction().update({})

    assert result.failed is True
    assert result.reason == "action_not_started"


def test_default_servo_channel_is_output_8() -> None:
    action = PayloadReleaseAction()
    action.start(
        {
            "payload_id": "payload_1",
            "target_id": "target_a",
        }
    )

    result = action.update({})

    assert action.channels == [8]
    assert result.detail["channels"] == [8]
    assert result.detail["servo_channels"] == [8]
    assert result.detail["servo_outputs"] == [{"channel": 8, "release_pwm": 1800, "hold_pwm": 1370}]
    assert "not RC input channel" in result.detail["channel_semantics"]


def test_single_servo_output_8_release_action() -> None:
    action = PayloadReleaseAction()
    action.start(_params(servo_outputs=[{"channel": 8, "release_pwm": 1200, "hold_pwm": 1700}]))

    result = action.update({"timestamp": 12.5})

    assert result.reason == "release_sent"
    assert result.done is False
    assert len(result.actions) == 1
    assert result.actions[0]["action_type"] == "set_servo"
    assert result.actions[0]["params"] == {"channel": 8, "pwm": 1200}
    assert result.actions[0]["once"] is True
    assert result.actions[0]["key"].endswith("_release_servo8")
    assert result.detail["release_time"] == pytest.approx(12.5)


def test_release_wait_seconds_uses_monotonic_and_overrides_update_count(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [10.0]
    monkeypatch.setattr(payload_release_module.time, "monotonic", lambda: now[0])
    action = PayloadReleaseAction()
    action.start(_params(release_wait_s=1.0, release_wait_updates=1))
    release = action.update({})
    assert len(release.actions) == 1
    assert release.detail["wait_mode"] == "seconds"
    now[0] = 10.99
    waiting = action.update({})
    assert not waiting.done and waiting.actions == []
    assert waiting.detail["release_elapsed_s"] == pytest.approx(.99)
    now[0] = 11.0
    held = action.update({})
    assert held.done and len(held.actions) == 1
    assert held.actions[0]["params"]["pwm"] == 1700
    assert action.update({}).actions == []


@pytest.mark.parametrize("release_wait_s", [0, -1, float("inf"), "not-a-number"])
def test_release_wait_seconds_must_be_positive_and_finite(release_wait_s) -> None:
    with pytest.raises(ValueError, match="release_wait_s"):
        PayloadReleaseAction().start(_params(release_wait_s=release_wait_s))


def test_single_servo_output_9_release_action() -> None:
    action = PayloadReleaseAction()
    action.start(_params(servo_outputs=[{"channel": 9, "release_pwm": 1700, "hold_pwm": 1200}]))

    result = action.update({})

    assert result.actions[0]["action_type"] == "set_servo"
    assert result.actions[0]["params"]["channel"] == 9
    assert result.actions[0]["params"]["pwm"] == 1700


def test_dual_servo_outputs_use_per_channel_release_and_hold_pwm() -> None:
    action = PayloadReleaseAction()
    action.start(
        _params(
            servo_outputs=[
                {"channel": 8, "release_pwm": 1200, "hold_pwm": 1700},
                {"channel": 9, "release_pwm": 1700, "hold_pwm": 1200},
            ],
            servo_channels=[1],
            channels=[2],
        )
    )

    release = action.update({})
    for _ in range(5):
        hold = action.update({})

    assert [item["params"] for item in release.actions] == [
        {"channel": 8, "pwm": 1200},
        {"channel": 9, "pwm": 1700},
    ]
    assert release.actions[0]["key"].endswith("_release_servo8")
    assert release.actions[1]["key"].endswith("_release_servo9")
    assert hold.actions[0]["params"] == {"channel": 8, "pwm": 1700}
    assert hold.actions[1]["params"] == {"channel": 9, "pwm": 1200}
    assert hold.actions[0]["key"].endswith("_hold_servo8")
    assert hold.actions[1]["key"].endswith("_hold_servo9")


def test_legacy_channels_are_compatible_servo_outputs() -> None:
    action = PayloadReleaseAction()
    action.start(_params(servo_outputs=None, channels=[14], release_pwm=1300, hold_pwm=1800))

    result = action.update({})

    assert result.actions[0]["params"] == {"channel": 14, "pwm": 1300}
    assert result.actions[0]["key"].endswith("_release_servo14")
    assert not result.actions[0]["key"].endswith("_release_rc14")


def test_servo_outputs_take_priority_over_legacy_channels() -> None:
    action = PayloadReleaseAction()
    action.start(
        _params(
            servo_outputs=[{"channel": 9, "release_pwm": 1700, "hold_pwm": 1200}],
            servo_channels=[8],
            channels=[14],
            release_pwm=1300,
            hold_pwm=1800,
        )
    )

    result = action.update({})

    assert result.actions[0]["params"] == {"channel": 9, "pwm": 1700}


def test_servo_channels_legacy_format_uses_global_pwm() -> None:
    action = PayloadReleaseAction()
    action.start(
        _params(
            servo_outputs=None,
            servo_channels=[8, 9],
            channels=[14],
            release_pwm=1300,
            hold_pwm=1800,
        )
    )

    release = action.update({})
    for _ in range(5):
        hold = action.update({})

    assert [item["params"] for item in release.actions] == [
        {"channel": 8, "pwm": 1300},
        {"channel": 9, "pwm": 1300},
    ]
    assert [item["params"] for item in hold.actions] == [
        {"channel": 8, "pwm": 1800},
        {"channel": 9, "pwm": 1800},
    ]


def test_channels_are_deduplicated_in_order() -> None:
    action = PayloadReleaseAction()
    action.start(
        _params(
            servo_outputs=[
                {"channel": 8, "release_pwm": 1200, "hold_pwm": 1700},
                {"channel": 9, "release_pwm": 1700, "hold_pwm": 1200},
                {"channel": 8, "release_pwm": 1300, "hold_pwm": 1800},
            ]
        )
    )

    result = action.update({})

    assert result.detail["channels"] == [8, 9]
    assert result.detail["servo_channels"] == [8, 9]


def test_wait_phase_does_not_repeat_release() -> None:
    action = PayloadReleaseAction()
    action.start(_params(release_wait_updates=3))

    first = action.update({})
    second = action.update({})

    assert first.reason == "release_sent"
    assert second.reason == "release_waiting"
    assert second.actions == []
    assert second.detail["wait_updates"] == 1


def test_release_and_wait_continuously_publish_zero_velocity_command() -> None:
    action = PayloadReleaseAction()
    action.start(_params(release_wait_updates=3))

    release = action.update({"field_heading_yaw_rad": 1.2})
    waiting = action.update({"field_heading_yaw_rad": 1.2})

    for result in (release, waiting):
        command = result.detail["command"]
        assert command["type"] == "flight_command"
        assert command["vx_cmd"] == 0.0
        assert command["vy_cmd"] == 0.0
        assert command["vz_cmd"] == 0.0
        assert command["valid"] is True
        assert command["enable_body"] is True
        assert command["yaw_hold_rad"] == pytest.approx(1.2)


def test_dispatch_release_sends_servo_and_zero_velocity_same_result() -> None:
    calls = []

    class FakeLink:
        def set_servo_output_pwm(self, *, servo_output, pwm, priority):
            calls.append(("servo", servo_output, pwm, priority))

        def send_velocity_command(self, vx, vy, vz, *, frame, yaw_rad=None):
            calls.append(("velocity", vx, vy, vz, frame, yaw_rad))

    action = PayloadReleaseAction()
    action.start(_params(servo_outputs=[
        {"channel": 9, "release_pwm": 1745, "hold_pwm": 1325},
    ]))
    result = action.update({"field_heading_yaw_rad": 1.2})
    dispatcher = ActionDispatcher(test_source="test")
    from execution.authorization import RunAuthorization
    dispatcher.set_authorization(RunAuthorization.create(
        operator="test", scope_type="action", scope_name="payload_release",
        target_source="sitl", allowed_actions={"payload_release"},
    ))

    dispatch = dispatcher.dispatch_result(
        result,
        action_name="payload_release",
        send_commands=True,
        link_manager=FakeLink(),
    )

    assert [item["action_type"] for item in dispatch["accepted"]] == ["set_servo", "flight_command"]
    assert calls[0][:3] == ("servo", 9, 1745)
    assert calls[1][0:4] == ("velocity", 0.0, 0.0, 0.0)


def test_wait_completion_sends_hold_pwm_once_and_finishes() -> None:
    action = PayloadReleaseAction()
    action.start(_params(release_wait_updates=2))

    action.update({})
    waiting = action.update({})
    done = action.update({})
    after = action.update({})

    assert waiting.reason == "release_waiting"
    assert done.done is True
    assert done.reason == "payload_released"
    assert done.actions[0]["action_type"] == "set_servo"
    assert done.actions[0]["params"] == {"channel": 8, "pwm": 1700}
    assert done.actions[0]["key"].endswith("_hold_servo8")
    assert after.done is True
    assert after.reason == "payload_released"
    assert after.actions == []


def test_release_time_param_takes_priority() -> None:
    action = PayloadReleaseAction()
    action.start(_params(release_time="planned_t"))

    result = action.update({"timestamp": 99.0})

    assert result.detail["release_time"] == "planned_t"


def test_release_time_falls_back_to_context_time() -> None:
    action = PayloadReleaseAction()
    action.start(_params())

    result = action.update({"time": 3.5})

    assert result.detail["release_time"] == pytest.approx(3.5)


def test_stop_then_update_returns_stopped_without_hold_action() -> None:
    action = PayloadReleaseAction()
    action.start(_params())
    action.stop()

    result = action.update({})

    assert result.done is True
    assert result.reason == "stopped"
    assert result.actions == []


def test_disabled_release_completes_without_servo_effects() -> None:
    action = PayloadReleaseAction()
    action.start(_params(enabled=False))

    result = action.update({})

    assert result.done and not result.failed
    assert result.reason == "payload_release_skipped"
    assert result.actions == []
    assert result.detail["enabled"] is False


def test_reset_then_update_returns_action_not_started() -> None:
    action = PayloadReleaseAction()
    action.start(_params())
    action.reset()

    result = action.update({})

    assert result.failed is True
    assert result.reason == "action_not_started"


def test_priority_default_and_custom_priority() -> None:
    default_action = PayloadReleaseAction()
    default_action.start(_params())
    custom_action = PayloadReleaseAction()
    custom_action.start(_params(priority=7))

    default_result = default_action.update({})
    custom_result = custom_action.update({})

    assert default_result.actions[0]["priority"] == 3
    assert custom_result.actions[0]["priority"] == 7


def test_default_and_custom_key() -> None:
    default_action = PayloadReleaseAction()
    default_action.start(_params(payload_id="p1", target_id="t1"))
    custom_action = PayloadReleaseAction()
    custom_action.start(_params(key="custom_release"))

    default_result = default_action.update({})
    custom_result = custom_action.update({})

    assert default_result.actions[0]["key"] == "payload_release_p1_t1_release_servo8"
    assert custom_result.actions[0]["key"] == "custom_release_release_servo8"


def test_output_is_plain_json_serializable_set_servo_dict() -> None:
    action = PayloadReleaseAction()
    action.start(
        _params(
            servo_outputs=[
                {"channel": 8, "release_pwm": 1200, "hold_pwm": 1700},
                {"channel": 9, "release_pwm": 1700, "hold_pwm": 1200},
            ]
        )
    )

    result = action.update({"timestamp": 1.0})

    assert all(item["action_type"] == "set_servo" for item in result.actions)
    assert all(item["action_type"] != "release_payload" for item in result.actions)
    assert result.detail["payload_id"] == "payload_1"
    assert result.detail["target_id"] == "target_a"
    json.dumps(result.to_dict())
