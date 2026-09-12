from __future__ import annotations

from unittest.mock import patch

from application.action_runtime import ActionRuntimeService


def test_clear_navigation_queue_default_calls_stop_and_clear() -> None:
    """Default path uses atomic stop_and_clear, NOT stop_body + clear."""
    calls: list[str] = []

    class FakeLink:
        def stop_body_velocity_and_clear(self) -> None:
            calls.append("stop_body_velocity_and_clear")

        def stop_body_velocity(self) -> None:
            calls.append("stop_body_velocity")

        def clear_continuous_commands(self) -> None:
            calls.append("clear_continuous_commands")

        def clear_pending_local_position_actions(self) -> None:
            calls.append("clear_pending_local_position_actions")

    ActionRuntimeService.clear_navigation_queue(FakeLink())

    # Must call stop_body_velocity_and_clear, NOT the separate pair
    assert "stop_body_velocity_and_clear" in calls
    assert "stop_body_velocity" not in calls, (
        "default path must NOT call stop_body_velocity; use stop_and_clear"
    )
    assert "clear_continuous_commands" not in calls, (
        "default path must NOT call clear_continuous_commands right after stop; "
        "stop_and_clear handles atomic clear internally"
    )
    assert "clear_pending_local_position_actions" in calls


def test_clear_navigation_queue_fallback_without_stop_and_clear() -> None:
    """If stop_body_velocity_and_clear is unavailable, fallback to stop_body WITHOUT clear."""
    calls: list[str] = []

    class FakeLink:
        def stop_body_velocity(self) -> None:
            calls.append("stop_body_velocity")

        def clear_continuous_commands(self) -> None:
            calls.append("clear_continuous_commands")

        def clear_pending_local_position_actions(self) -> None:
            calls.append("clear_pending_local_position_actions")

    ActionRuntimeService.clear_navigation_queue(FakeLink())

    # Fallback: no stop_and_clear → stop_body is called, but NOT followed by clear
    assert "stop_body_velocity" in calls
    assert "clear_continuous_commands" not in calls, (
        "fallback must NOT immediately clear STOP; leave it queued for CommandSender"
    )
    assert "clear_pending_local_position_actions" in calls


def test_clear_navigation_queue_can_leave_stop_for_payload_transition() -> None:
    """leave_stop_queued=True preserves old semantics."""
    calls: list[str] = []

    class FakeLink:
        def stop_body_velocity_and_clear(self) -> None:
            calls.append("stop_body_velocity_and_clear")

        def stop_body_velocity(self) -> None:
            calls.append("stop_body_velocity")

        def clear_continuous_commands(self) -> None:
            calls.append("clear_continuous_commands")

        def clear_pending_local_position_actions(self) -> None:
            calls.append("clear_pending_local_position_actions")

        def hold_current_local_position(self) -> None:
            calls.append("hold_current_local_position")

    ActionRuntimeService.clear_navigation_queue(
        FakeLink(),
        hold_current=True,
        leave_stop_queued=True,
    )

    # leave_stop_queued: clear first, then persistent STOP — NO stop_and_clear
    assert calls == [
        "clear_continuous_commands",
        "clear_pending_local_position_actions",
        "stop_body_velocity",
        "hold_current_local_position",
    ]


# ── SITL 前安全修复：stop() / reset() 路径确认 ──────────────────────


def test_stop_calls_clear_navigation_queue_with_stop_and_clear() -> None:
    """ActionRuntimeService.stop() triggers stop_body_velocity_and_clear via clear_navigation_queue."""
    calls: list[str] = []

    class FakeLink:
        def stop_body_velocity_and_clear(self) -> None:
            calls.append("stop_body_velocity_and_clear")

        def stop_body_velocity(self) -> None:
            calls.append("stop_body_velocity")

        def clear_continuous_commands(self) -> None:
            calls.append("clear_continuous_commands")

        def clear_pending_local_position_actions(self) -> None:
            calls.append("clear_pending_local_position_actions")

    from execution.dispatcher import ActionDispatcher
    from missions.common.actions.runner import ActionRunner

    svc = ActionRuntimeService(runner=ActionRunner(), dispatcher=ActionDispatcher(test_source="test"))
    svc.stop(FakeLink())

    assert "stop_body_velocity_and_clear" in calls
    assert "stop_body_velocity" not in calls
    assert "clear_continuous_commands" not in calls


def test_reset_calls_clear_navigation_queue_with_stop_and_clear() -> None:
    """ActionRuntimeService.reset() triggers stop_body_velocity_and_clear via clear_navigation_queue."""
    calls: list[str] = []

    class FakeLink:
        def stop_body_velocity_and_clear(self) -> None:
            calls.append("stop_body_velocity_and_clear")

        def stop_body_velocity(self) -> None:
            calls.append("stop_body_velocity")

        def clear_continuous_commands(self) -> None:
            calls.append("clear_continuous_commands")

        def clear_pending_local_position_actions(self) -> None:
            calls.append("clear_pending_local_position_actions")

    from execution.dispatcher import ActionDispatcher
    from missions.common.actions.runner import ActionRunner

    svc = ActionRuntimeService(runner=ActionRunner(), dispatcher=ActionDispatcher(test_source="test"))
    svc.reset(FakeLink())

    assert "stop_body_velocity_and_clear" in calls
    assert "stop_body_velocity" not in calls
    assert "clear_continuous_commands" not in calls


def test_switch_running_action_stops_old_action_and_clears_navigation() -> None:
    """Starting a different Action characterizes the current switch ordering."""
    from execution.dispatcher import ActionDispatcher
    from missions.common.actions.base import ActionModule
    from missions.common.actions.registry import ActionRegistry
    from missions.common.actions.result import ActionResult
    from missions.common.actions.runner import ActionRunner

    action_events: list[str] = []
    link_events: list[str] = []

    class FirstAction(ActionModule):
        def start(self, params=None):
            action_events.append("first.start")

        def update(self, context=None):
            return ActionResult()

        def stop(self):
            action_events.append("first.stop")

        def reset(self):
            action_events.append("first.reset")

    class SecondAction(ActionModule):
        def start(self, params=None):
            action_events.append("second.start")

        def update(self, context=None):
            return ActionResult()

        def stop(self):
            action_events.append("second.stop")

        def reset(self):
            action_events.append("second.reset")

    class FakeLink:
        def stop_body_velocity_and_clear(self):
            link_events.append("stop_body_velocity_and_clear")

        def clear_pending_local_position_actions(self):
            link_events.append("clear_pending_local_position_actions")

    registry = ActionRegistry()
    registry.register("first", FirstAction)
    registry.register("second", SecondAction)
    service = ActionRuntimeService(
        runner=ActionRunner(registry),
        dispatcher=ActionDispatcher(test_source="test"),
    )
    link = FakeLink()

    service.start("first", link_manager=link)
    service.start("second", link_manager=link)

    assert action_events == ["first.start", "first.stop", "first.reset", "second.start"]
    assert link_events == [
        "stop_body_velocity_and_clear",
        "clear_pending_local_position_actions",
        "stop_body_velocity_and_clear",
        "clear_pending_local_position_actions",
    ]
    assert service.action_name == "second"
    assert service.runner.state == "running"


def test_start_failure_replaces_previous_result_for_mission_orchestrator() -> None:
    """A failed start cannot leave the preceding Action's success visible."""
    from execution.dispatcher import ActionDispatcher
    from missions.common.actions.base import ActionModule
    from missions.common.actions.registry import ActionRegistry
    from missions.common.actions.runner import ActionRunner

    class StartFails(ActionModule):
        def start(self, params=None):
            raise ValueError("missing target")

        def update(self, context=None):
            raise AssertionError("must not update after start failure")

        def stop(self):
            pass

        def reset(self):
            pass

    registry = ActionRegistry()
    registry.register("start_fails", StartFails)
    service = ActionRuntimeService(
        runner=ActionRunner(registry),
        dispatcher=ActionDispatcher(test_source="test"),
    )
    service.last_result = {"done": True, "failed": False, "reason": "previous_done"}

    result = service.start("start_fails")

    assert result.failed is True
    assert service.last_result is not None
    assert service.last_result["failed"] is True
    assert service.last_result["reason"] == "action_start_failed"


def test_mission_handles_a_start_failure_instead_of_reusing_previous_success() -> None:
    from execution.dispatcher import ActionDispatcher
    from missions.common.actions.base import ActionModule
    from missions.common.actions.registry import ActionRegistry
    from missions.common.actions.result import ActionResult
    from missions.common.actions.runner import ActionRunner
    from missions.engine import MissionActionStep, MissionOrchestrator

    class Done(ActionModule):
        def start(self, params=None):
            pass

        def update(self, context=None):
            return ActionResult(done=True, reason="done")

        def stop(self):
            pass

        def reset(self):
            pass

    class StartFails(Done):
        def start(self, params=None):
            raise ValueError("bad start")

    registry = ActionRegistry()
    registry.register("done", Done)
    registry.register("start_fails", StartFails)
    service = ActionRuntimeService(
        runner=ActionRunner(registry),
        dispatcher=ActionDispatcher(test_source="test"),
    )
    mission = MissionOrchestrator(service, [
        MissionActionStep("done", label="first"),
        MissionActionStep(
            "start_fails",
            label="broken",
            on_failed={"action": "jump_to", "target": "recovery"},
        ),
        MissionActionStep("done", label="recovery"),
    ])

    mission.start()
    mission.tick({})  # first Action completes; broken Action then fails to start
    status = mission.tick({})

    assert status.current_action == "done"
    assert status.current_index == 2
    assert status.reason == "jump_to"
    assert status.detail["failed_action_result"]["reason"] == "action_start_failed"


def test_failed_atomic_goto_clears_global_navigation_and_holds_current() -> None:
    """Direct Action Lab failure uses the same queue clear + hold primitive as Mission."""
    from execution.dispatcher import ActionDispatcher
    from missions.common.actions.registry import ActionRegistry
    from missions.common.actions.runner import ActionRunner
    from missions.common.actions.base import ActionModule
    from missions.common.actions.result import ActionResult

    class FailingRecon(ActionModule):
        def start(self, params=None): pass
        def update(self, context=None): return ActionResult(failed=True, reason="goto_failed")
        def stop(self): pass
        def reset(self): pass

    calls: list[str] = []
    class FakeLink:
        def stop_body_velocity_and_clear(self): calls.append("stop_body_velocity_and_clear")
        def clear_pending_local_position_actions(self): calls.append("clear_pending_local_position_actions")
        def hold_current_local_position(self): calls.append("hold_current_local_position")

    registry = ActionRegistry()
    registry.register("goto_waypoint", FailingRecon)
    service = ActionRuntimeService(runner=ActionRunner(registry), dispatcher=ActionDispatcher(test_source="test"))
    service.start("goto_waypoint", {"field_x_m": 0.0, "field_y_m": 0.0, "altitude_m": 3.0}, link_manager=None)
    service.tick({}, link_manager=FakeLink(), send_commands=False)

    assert calls == [
        "stop_body_velocity_and_clear",
        "clear_pending_local_position_actions",
        "hold_current_local_position",
    ]
