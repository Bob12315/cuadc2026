from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path", [
    "tools/telemetry_debug.py",
    "tools/fusion_debug.py",
    "telemetry_link/command_dispatcher.py",
    "scripts/config/save_rk3588_real.sh",
    "scripts/config/save_rk3588_sitl.sh",
])
def test_retired_operator_and_profile_entrypoints_are_not_shipped(path: str) -> None:
    assert not (ROOT / path).exists(), f"retired entrypoint can be invoked: {path}"
