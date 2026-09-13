from __future__ import annotations

import threading
from types import SimpleNamespace

from application.mission_service import ACTION_MISSION_TICK_INTERVAL_S, MissionApplicationService
from execution.safety_config import load_safety_config


def test_action_tick_refreshes_well_inside_continuous_deadman() -> None:
    """A normal continuous action must not race its own safety watchdog."""
    assert ACTION_MISSION_TICK_INTERVAL_S * 2 < load_safety_config().continuous_deadman_s


def test_background_tick_advances_running_action_lab_without_action_mission() -> None:
    service = MissionApplicationService.__new__(MissionApplicationService)
    service.action_runtime_lock = threading.RLock()
    service.action_mission_orchestrator = None
    service._action_mission_next_tick_monotonic = None
    service.action_runtime = SimpleNamespace(runner=SimpleNamespace(state="running"))
    calls: list[str] = []
    service.action_lab_tick = lambda: calls.append("action_lab")

    assert service._tick_action_mission_in_background(now_monotonic=10.0) is True
    assert calls == ["action_lab"]
    assert service._tick_action_mission_in_background(
        now_monotonic=10.0 + ACTION_MISSION_TICK_INTERVAL_S / 2
    ) is False
