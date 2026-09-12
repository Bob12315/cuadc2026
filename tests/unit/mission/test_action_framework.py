from __future__ import annotations

import pytest

from missions.common.actions import (
    ActionModule,
    ActionRegistry,
    ActionResult,
    ActionRunner,
)


class FakeAction(ActionModule):
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.reset_called = False
        self.updates = 0

    def start(self, params: dict[str, object] | None = None) -> None:
        self.started = True
        self.params = params or {}

    def update(self, context: dict[str, object] | None = None) -> ActionResult:
        self.updates += 1
        if self.updates >= 2:
            return ActionResult(effects=ActionResult.typed([{"action_type": "yolo_lock_target", "params": {"track_id": 2}}]), done=True, detail={"updates": self.updates})
        return ActionResult(effects=ActionResult.typed([{"action_type": "yolo_lock_target", "params": {"track_id": 1}}]), detail={"context": context or {}})

    def stop(self) -> None:
        self.stopped = True

    def reset(self) -> None:
        self.reset_called = True


class InvalidResultAction(FakeAction):
    def update(self, context: dict[str, object] | None = None):
        return {"not": "an ActionResult"}


class StartErrorAction(FakeAction):
    def start(self, params: dict[str, object] | None = None) -> None:
        raise RuntimeError("start exploded")


class UpdateErrorAction(FakeAction):
    def update(self, context: dict[str, object] | None = None) -> ActionResult:
        raise RuntimeError("update exploded")


class StopErrorAction(FakeAction):
    def stop(self) -> None:
        raise RuntimeError("stop exploded")


class ResetErrorAction(FakeAction):
    def reset(self) -> None:
        raise RuntimeError("reset exploded")


class NotAnAction:
    pass


def test_action_result_defaults_are_not_shared() -> None:
    first = ActionResult()
    second = ActionResult()

    assert first.actions is not second.actions
    assert first.detail is not second.detail


def test_action_result_to_dict() -> None:
    result = ActionResult(
        effects=ActionResult.typed([{"action_type": "yolo_lock_target", "params": {"track_id": 1}}]),
        done=True,
        failed=False,
        reason="ok",
        detail={"value": 1},
    )

    assert result.to_dict() == {
        "actions": [{"action_type": "yolo_lock_target", "params": {"track_id": 1}, "key": "", "priority": 5, "once": False}],
        "done": True,
        "failed": False,
        "reason": "ok",
        "output": {},
        "detail": {"value": 1},
    }


def test_action_registry_registers_lists_gets_and_creates() -> None:
    registry = ActionRegistry()

    registry.register("fake", FakeAction)

    assert registry.list() == ["fake"]
    assert registry.get("fake") is FakeAction
    assert isinstance(registry.create("fake"), FakeAction)


def test_action_registry_rejects_duplicate_unknown_and_invalid_class() -> None:
    registry = ActionRegistry()
    registry.register("fake", FakeAction)

    with pytest.raises(ValueError):
        registry.register("fake", FakeAction)
    with pytest.raises(KeyError):
        registry.get("missing")
    with pytest.raises(KeyError):
        registry.create("missing")
    with pytest.raises(TypeError):
        registry.register("bad", NotAnAction)


def test_action_runner_normal_lifecycle() -> None:
    registry = ActionRegistry()
    registry.register("fake", FakeAction)
    runner = ActionRunner(registry)

    start = runner.start("fake", {"speed": 1})
    first = runner.update({"tick": 1})
    second = runner.update({"tick": 2})

    assert start.failed is False
    assert runner.status()["state"] == "succeeded"
    assert first.actions[0]["params"]["track_id"] == 1
    assert second.done is True
    assert second.actions[0]["params"]["track_id"] == 2

    runner.reset()

    assert runner.status()["state"] == "idle"
    assert runner.status()["action_name"] is None


def test_action_runner_invalid_states_do_not_crash() -> None:
    registry = ActionRegistry()
    registry.register("fake", FakeAction)
    runner = ActionRunner(registry)

    assert runner.update().reason == "no_active_action"
    assert runner.stop().reason == "no_active_action"
    assert runner.status()["state"] == "idle"
    runner.reset()
    runner.reset()

    runner.start("fake")
    duplicate = runner.start("fake")

    assert duplicate.failed is True
    assert duplicate.reason == "action_already_running"


def test_action_runner_invalid_action_result_fails() -> None:
    registry = ActionRegistry()
    registry.register("invalid", InvalidResultAction)
    runner = ActionRunner(registry)

    runner.start("invalid")
    result = runner.update()

    assert result.failed is True
    assert result.reason == "invalid_action_result"
    assert runner.status()["state"] == "failed"


def test_action_runner_unknown_action_does_not_raise() -> None:
    runner = ActionRunner(ActionRegistry())

    result = runner.start("missing")

    assert result.failed is True
    assert result.reason == "unknown_action"
    assert result.detail["action_name"] == "missing"
    assert runner.status()["state"] == "idle"


def test_action_runner_start_exception_does_not_raise() -> None:
    registry = ActionRegistry()
    registry.register("start_error", StartErrorAction)
    runner = ActionRunner(registry)

    result = runner.start("start_error")

    assert result.failed is True
    assert result.reason == "action_start_failed"
    assert result.detail["action_name"] == "start_error"
    assert runner.status()["state"] == "failed"


def test_action_runner_update_exception_does_not_raise() -> None:
    registry = ActionRegistry()
    registry.register("update_error", UpdateErrorAction)
    runner = ActionRunner(registry)

    runner.start("update_error")
    result = runner.update()

    assert result.failed is True
    assert result.reason == "action_update_failed"
    assert result.detail["action_name"] == "update_error"
    assert runner.status()["state"] == "failed"


def test_action_runner_stop_exception_does_not_raise() -> None:
    registry = ActionRegistry()
    registry.register("stop_error", StopErrorAction)
    runner = ActionRunner(registry)

    runner.start("stop_error")
    result = runner.stop()

    assert result.failed is True
    assert result.reason == "action_stop_failed"
    assert result.detail["action_name"] == "stop_error"
    assert runner.status()["state"] == "failed"


def test_action_runner_reset_exception_clears_state() -> None:
    registry = ActionRegistry()
    registry.register("reset_error", ResetErrorAction)
    runner = ActionRunner(registry)

    runner.start("reset_error")
    result = runner.reset()

    assert result.failed is True
    assert result.reason == "action_reset_failed"
    assert runner.status()["state"] == "idle"
    assert runner.current_action is None
    assert runner.action_name is None
