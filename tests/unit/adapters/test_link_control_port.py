from __future__ import annotations

from types import SimpleNamespace

from telemetry_link.link_manager import LinkManager
from telemetry_link.ports import VehicleStateAdapter


def test_vehicle_state_adapter_has_no_source_mutation_surface() -> None:
    adapter = VehicleStateAdapter(lambda: None)
    assert not hasattr(adapter, "switch_active_source")
    snapshot = adapter.snapshot()
    assert snapshot.connected is False
    assert snapshot.local_north_m is None


def test_link_control_revision_conflict_does_not_switch() -> None:
    manager = LinkManager.__new__(LinkManager)
    manager.cfg = SimpleNamespace(data_source="dual")
    manager.logger = SimpleNamespace(warning=lambda *args: None, info=lambda *args: None)
    import threading
    manager.active_lock = threading.Lock()
    manager.active_source = "real"
    manager._source_revision = 3
    manager.runtimes = {
        "real": SimpleNamespace(command_broker=None),
        "sitl": SimpleNamespace(command_broker=None),
    }
    conflict = manager.activate_source("sitl", expected_revision=2)
    assert conflict.accepted is False
    assert manager.active_source == "real"
    accepted = manager.activate_source("sitl", expected_revision=3)
    assert accepted.accepted is True
    assert accepted.revision == 4
