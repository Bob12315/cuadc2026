from pathlib import Path

from app.config import build_arg_parser, load_app_config, load_telemetry_config
from application.runner import SystemRunner
from application.web_services import WebServices
from telemetry_link.config import DEFAULT_CONFIG_PATH, load_config_file
from web_ui.server import create_app


def _all_routes(app):
    pending = list(app.routes)
    while pending:
        route = pending.pop(0)
        children = getattr(route, "routes", None)
        if children is None:
            children = getattr(getattr(route, "original_router", None), "routes", None)
        if children is not None:
            pending[0:0] = list(children)
        else:
            yield route


def test_system_runner_does_not_import_legacy_mission_or_stage_entrypoints() -> None:
    source = (Path(__file__).parents[2] / "application" / "runner.py").read_text(
        encoding="utf-8"
    )

    assert "app.mission_runner" not in source
    assert "app.stage_registry" not in source
    assert "MissionRunner" not in source
    assert "StageRegistry" not in source


def test_app_config_uses_action_lab_only_without_legacy_mission_loading():
    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])

    config = load_app_config(args)

    assert not hasattr(config, "mission_enabled")
    assert not hasattr(config, "mission_name")


def test_app_config_loads_current_web_and_terminal_ui_settings() -> None:
    args = build_arg_parser().parse_args(["--send-commands", "false"])

    config = load_app_config(args)

    assert config.ui.web_enabled is True
    assert config.ui.web_port == 8080


def test_app_config_reuses_telemetry_link_config_parser() -> None:
    assert load_telemetry_config(DEFAULT_CONFIG_PATH) == load_config_file(DEFAULT_CONFIG_PATH)


def test_app_help_does_not_expose_removed_control_config() -> None:
    assert "--control-config" not in build_arg_parser().format_help()


def test_system_runner_snapshot_works_without_mission_runtime():
    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])
    config = load_app_config(args)

    runner = SystemRunner(config)
    snapshot = runner.web_status_snapshot()

    assert snapshot["mission"] == "action_lab_only"
    assert snapshot["stage"] == "NO_MISSION"
    assert "stage_modes" not in snapshot
    assert "mission_stage_selection" not in snapshot
    assert snapshot["actions"] == []
    assert snapshot["action_lab"]["enabled"] is True
    assert snapshot["action_lab"]["run_authorized"] is False
    assert snapshot["action_lab"]["run_id"] is None
    assert snapshot["action_lab"]["dispatch_effective"] is False
    assert snapshot["action_lab"]["note"] == "action_dispatch_not_enabled"
    # PR F: action_mission is not_configured by default
    assert snapshot["action_mission"]["enabled"] is False
    assert snapshot["action_mission"]["reason"] == "not_configured"


def test_system_runner_run_starts_web_ui_without_mission_runtime(monkeypatch) -> None:
    args = build_arg_parser().parse_args(["--run-seconds", "0", "--no-yolo-udp"])
    config = load_app_config(args)
    runner = SystemRunner(config)
    started = []

    class FakeWebUiServer:
        def __init__(self, web_services, ui_config) -> None:
            assert isinstance(web_services, WebServices)
            app = create_app(web_services, ui_config)
            status_route = next(
                route
                for route in _all_routes(app)
                if getattr(route, "path", "") == "/api/status"
            )
            assert status_route.endpoint()["mission"] == "action_lab_only"

        def start(self) -> None:
            started.append(True)

        def stop(self) -> None:
            return None

    monkeypatch.setattr("web_ui.server.WebUiServer", FakeWebUiServer)
    monkeypatch.setattr(runner.services, "start", lambda: None)
    monkeypatch.setattr(runner.services, "stop", lambda: None)

    runner.run()

    assert started == [True]


def test_action_mission_status_payload_not_configured_by_default() -> None:
    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])
    config = load_app_config(args)
    runner = SystemRunner(config)

    payload = runner.action_mission_status_payload()
    assert payload["enabled"] is False
    assert payload["running"] is False
    assert payload["done"] is False
    assert payload["failed"] is False
    assert payload["current_action"] is None
    assert payload["reason"] == "not_configured"


def test_configure_action_mission_sets_enabled() -> None:
    from missions.engine import MissionActionStep

    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])
    config = load_app_config(args)
    runner = SystemRunner(config)

    steps = [MissionActionStep("goto_waypoint", {"x": 1.0})]
    runner.configure_action_mission(steps)

    payload = runner.action_mission_status_payload()
    assert payload["enabled"] is True
    assert payload["current_action"] == "goto_waypoint"
    assert runner.action_mission_orchestrator is not None


def test_action_mission_tick_does_not_call_when_not_configured() -> None:
    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])
    config = load_app_config(args)
    runner = SystemRunner(config)

    result = runner.action_mission_tick()
    assert result["enabled"] is False
    assert result["running"] is False


def test_action_mission_start_stop_reset_lifecycle() -> None:
    from missions.engine import MissionActionStep

    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])
    config = load_app_config(args)
    runner = SystemRunner(config)

    runner.configure_action_mission([MissionActionStep("change_speed", {"speed_mps": 1.0})])

    started = runner.action_mission_start(authorize=True, target_source="sitl")
    assert started["enabled"] is True
    assert started["running"] is True

    stopped = runner.action_mission_stop()
    assert stopped["running"] is False
    assert stopped["reason"] == "stopped"

    reset = runner.action_mission_reset()
    assert reset["running"] is False
    assert reset["reason"] == "reset"


def test_action_mission_web_api_lifecycle() -> None:
    from types import SimpleNamespace
    from web_ui.dto import (
        ActionMissionConfigureAndStartRequest,
        ActionMissionConfigureRequest,
        ActionMissionStepRequest,
        RunStartRequest,
    )

    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])
    config = load_app_config(args)
    runner = SystemRunner(config)
    app = create_app(WebServices.from_runner(runner), config.ui)

    def endpoint(path: str):
        return next(route.endpoint for route in _all_routes(app) if getattr(route, "path", "") == path)

    status = endpoint("/api/action-mission/status")()
    assert status["ok"] is True
    assert status["action_mission"]["reason"] == "not_configured"

    configured = endpoint("/api/action-mission/configure")(
        ActionMissionConfigureRequest(
            steps=[
                    ActionMissionStepRequest(name="change_speed", params={"speed_mps": 1.0}),
            ],
        )
    )
    assert configured["ok"] is True
    assert configured["action_mission"]["enabled"] is True

    configured_and_started = endpoint("/api/action-mission/configure-and-start")(
        ActionMissionConfigureAndStartRequest(
            steps=[
                ActionMissionStepRequest(name="change_speed", params={"speed_mps": 1.0}),
            ],
            authorize=True,
            target_source="real",
        ),
        SimpleNamespace(state=SimpleNamespace(identity=SimpleNamespace(operator="test"))),
    )
    assert configured_and_started["ok"] is True
    assert configured_and_started["action_mission"]["running"] is True
    assert configured_and_started["action_mission"]["current_action"] == "change_speed"

    started = endpoint("/api/action-mission/start")(
        RunStartRequest(authorize=True, target_source="real"),
        SimpleNamespace(state=SimpleNamespace(identity=SimpleNamespace(operator="test"))),
    )
    assert started["action_mission"]["running"] is True

    stopped = endpoint("/api/action-mission/stop")()
    assert stopped["action_mission"]["running"] is False

    reset = endpoint("/api/action-mission/reset")()
    assert reset["action_mission"]["reason"] == "reset"

    runner.action_mission_orchestrator = None
    tick = endpoint("/api/action-mission/tick")()
    assert tick["ok"] is True
    assert tick["action_mission"]["reason"] == "not_configured"


def test_action_mission_step_request_accepts_save_as() -> None:
    from web_ui.dto import ActionMissionStepRequest

    request = ActionMissionStepRequest(name="gps_fuse_views", params={}, save_as="drop_scan")

    assert request.save_as == "drop_scan"


def test_action_mission_step_request_accepts_label_and_on_failed() -> None:
    from web_ui.dto import ActionMissionStepRequest

    request = ActionMissionStepRequest(
        name="target_lock",
        label="lock0",
        on_failed={"action": "retry_current", "max_attempts": 2},
    )

    assert request.label == "lock0"
    assert request.on_failed == {"action": "retry_current", "max_attempts": 2}


def test_action_lab_api_status_reports_missing_run_authorization():
    args = build_arg_parser().parse_args(["--run-seconds", "0.1", "--no-yolo-udp"])
    config = load_app_config(args)
    runner = SystemRunner(config)
    app = create_app(WebServices.from_runner(runner), config.ui)
    route = next(route for route in _all_routes(app) if getattr(route, "path", "") == "/api/actions/status")

    response = route.endpoint()

    assert response["ok"] is True
    assert response["action_lab"]["enabled"] is True
    assert response["action_lab"]["run_authorized"] is False
    assert response["action_lab"]["run_id"] is None
    assert response["action_lab"]["dispatch_effective"] is False


def test_no_uav_ui_imports_in_app_startup() -> None:
    """Verify that app startup does not import uav_ui (terminal UI removed)."""
    import subprocess, sys

    code = (
        "from application.runner import SystemRunner; "
        "from app.config import build_arg_parser, load_app_config; "
        "args = build_arg_parser().parse_args(['--run-seconds', '0.1', '--no-yolo-udp']); "
        "config = load_app_config(args); "
        "runner = SystemRunner(config); "
        "print('STARTUP_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=10,
    )
    assert "STARTUP_OK" in result.stdout, f"Startup failed: {result.stderr}"
