from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from telemetry_link.config import DEFAULT_CONFIG_PATH, _build_config
from telemetry_link.link_manager import LinkManager
from telemetry_link.models import ActionType
import telemetry_link.link_manager as link_manager_module
import telemetry_link.config as telemetry_config


@pytest.mark.parametrize("key,value", [
    ("legacy_writer_enabled", True), ("legacy_writer_enabled", False),
    ("v2_writer_enabled", True), ("v2_writer_enabled", False),
    ("ui_enabled", True), ("ui_enabled", False),
    ("action_cmd_retries", 3), ("action_retry_interval_sec", .5), ("control_send_rate_hz", 10),
    ("state_udp_enabled", True), ("state_udp_ip", "127.0.0.1"), ("state_udp_port", 5010),
])
def test_retired_transport_config_is_rejected(key: str, value: object) -> None:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
    for retired in ("legacy_writer_enabled", "v2_writer_enabled", "ui_enabled"):
        data.pop(retired, None)
    data[key] = value
    with pytest.raises(ValueError, match="retired telemetry config"):
        _build_config(data)


def test_standalone_transport_entry_is_removed() -> None:
    assert not hasattr(telemetry_config, "build_arg_parser")
    assert not hasattr(telemetry_config, "load_config")


def test_unconnected_transport_rejects_commands_without_a_fallback_queue(monkeypatch) -> None:
    monkeypatch.setattr(link_manager_module, "MavlinkClient", lambda _: SimpleNamespace())
    manager = LinkManager(_build_config(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())))
    for receipt in (manager.arm(), manager.land(), manager.send_body_velocity(1, 0, 0)):
        assert receipt.submission_state.value == "REJECTED"
        assert receipt.reason_code == "command_backend_unavailable"
    assert all(not hasattr(runtime, "command_queue") for runtime in manager.runtimes.values())


def test_removed_payload_interface_cannot_be_selected() -> None:
    assert not hasattr(LinkManager, "release_payload")
    assert "release_payload" not in {kind.value for kind in ActionType}
