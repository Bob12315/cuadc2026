"""Integration tests for Action dispatch — confirm_field_heading removed."""
from __future__ import annotations

from execution.dispatcher import ActionDispatcher
from missions.common.actions.takeoff import TakeoffAction


# ---------------------------------------------------------------------------
# ActionDispatcher — confirm_field_heading rejected
# ---------------------------------------------------------------------------


def test_dispatcher_rejects_confirm_field_heading():
    """ActionDispatcher must reject confirm_field_heading action type."""
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {"action_type": "confirm_field_heading", "params": {"yaw_rad": 0.5}},
        link_manager=None,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "unsupported_action_type"


def test_dispatcher_dispatches_set_servo():
    """ActionDispatcher rejects set_servo when no LinkManager is available."""
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {"action_type": "set_servo", "params": {"channel": 9, "pwm": 1500}},
        link_manager=None,
    )
    # set_servo may return status=error without link_manager, but must not be "unsupported"
    assert result.get("status") != "skipped" or "unsupported" not in str(result.get("reason", ""))


def test_dispatcher_dispatches_set_mode():
    """ActionDispatcher dispatches set_mode."""
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {"action_type": "set_mode", "params": {"mode": "GUIDED"}},
        link_manager=None,
    )
    assert result.get("status") != "skipped" or "unsupported" not in str(result.get("reason", ""))


def test_dispatcher_dispatches_arm():
    """ActionDispatcher dispatches arm."""
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {"action_type": "arm", "params": {}},
        link_manager=None,
    )
    assert result.get("status") != "skipped" or "unsupported" not in str(result.get("reason", ""))


def test_dispatcher_dispatches_takeoff():
    """ActionDispatcher dispatches takeoff."""
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {"action_type": "takeoff", "params": {"altitude_m": 3.0}},
        link_manager=None,
    )
    assert result.get("status") != "skipped" or "unsupported" not in str(result.get("reason", ""))


def test_dispatcher_dispatches_condition_yaw():
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {"action_type": "condition_yaw", "params": {"yaw_deg": 45.0}},
        link_manager=None,
    )
    assert result.get("status") != "skipped" or "unsupported" not in str(result.get("reason", ""))


def test_dispatcher_dispatches_land():
    """ActionDispatcher dispatches land."""
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {"action_type": "land", "params": {}},
        link_manager=None,
    )
    assert result.get("status") != "skipped" or "unsupported" not in str(result.get("reason", ""))


def test_dispatcher_dispatches_body_velocity():
    """ActionDispatcher dispatches flight_command / body_velocity."""
    dispatcher = ActionDispatcher(test_source="test")
    result = dispatcher._dispatch_action(
        {
            "action_type": "body_velocity",
            "params": {"vx_body_mps": 0.0, "vy_body_mps": 0.5, "vz_body_mps": 0.0,
                       "valid": True, "active": True},
        },
        link_manager=None,
    )
    assert result.get("status") != "skipped" or "unsupported" not in str(result.get("reason", ""))


# ---------------------------------------------------------------------------
# TakeoffAction — FIELD heading is commanded only after reaching takeoff altitude
# ---------------------------------------------------------------------------


def test_takeoff_first_action_is_set_mode():
    """TakeoffAction must emit set_mode first; it must not use the removed confirmation Action."""
    action = TakeoffAction()
    action.start({"altitude_m": 3.0})
    result = action.update({"field_heading_confirmed": True, "field_heading_yaw_rad": 0.0})
    assert result.reason == "set_mode_sent"
    assert len(result.actions) == 1
    assert result.actions[0]["action_type"] == "set_mode"
    assert result.actions[0]["action_type"] != "confirm_field_heading"


def test_takeoff_no_auto_confirm_attr():
    """TakeoffAction must not have auto_confirm_field_heading attribute."""
    action = TakeoffAction()
    action.start({"altitude_m": 3.0})
    assert not hasattr(action, "auto_confirm_field_heading"), (
        "TakeoffAction must not expose auto_confirm_field_heading"
    )


def test_takeoff_phase_is_set_mode():
    """TakeoffAction phase starts at set_mode, not confirm_field_heading."""
    action = TakeoffAction()
    action.start({})
    assert action.phase == "set_mode"
