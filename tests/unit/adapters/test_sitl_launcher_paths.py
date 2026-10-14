"""Exercise launcher path resolution without starting terminals or simulators."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[3] / "scripts/run_iris_gimbal_sitl.sh"
PATH_VARIABLES = (
    "ARDUPILOT_DIR",
    "GZ_REPO_DIR",
    "GZ_PARAM_FILE",
    "GZ_WORLD",
    "PAYLOAD_BRIDGE",
    "PAYLOAD_CONFIG",
)


def _executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("override_mode", ["default", "relative", "absolute"])
def test_launcher_resolves_external_paths_from_repo_and_keeps_sitl_output_in_runtime(
    tmp_path: Path, override_mode: str
) -> None:
    repo = tmp_path / "portable repo's checkout"
    launcher = repo / "scripts/run_iris_gimbal_sitl.sh"
    launcher.parent.mkdir(parents=True)
    shutil.copyfile(LAUNCHER, launcher)
    unrelated_cwd = tmp_path / "unrelated caller"
    unrelated_cwd.mkdir()
    fake_bin = tmp_path / "fake bin"
    terminal_log = tmp_path / "terminal.json"
    sitl_log = tmp_path / "sitl.json"
    _executable(
        fake_bin / "gnome-terminal",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['TERMINAL_LOG']).write_text(json.dumps(sys.argv[1:]))\n",
    )
    env = os.environ.copy()
    for variable in PATH_VARIABLES:
        env.pop(variable, None)
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}",
        TERMINAL_LOG=str(terminal_log),
        SITL_LOG=str(sitl_log),
        TERMINAL_HOLD="0",
        GCS_HOST="127.0.0.1",
        VIDEO_HOST="127.0.0.2",
    )
    if override_mode == "default":
        ardupilot = repo.parent / "ardupilot"
        gazebo = repo.parent / "gz_ws/src/ardupilot_gazebo"
    else:
        ardupilot = repo / "dependencies/ArduPilot's tree"
        gazebo = repo / "dependencies/Gazebo's tree"
        env.update(
            ARDUPILOT_DIR=(
                "dependencies/ArduPilot's tree"
                if override_mode == "relative"
                else str(ardupilot)
            ),
            GZ_REPO_DIR=(
                "dependencies/Gazebo's tree"
                if override_mode == "relative"
                else str(gazebo)
            ),
        )
    _executable(
        ardupilot / "Tools/autotest/sim_vehicle.py",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['SITL_LOG']).write_text(\n"
        "    json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}))\n",
    )
    result = subprocess.run(
        ["bash", str(launcher)],
        cwd=unrelated_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    terminal_args = json.loads(terminal_log.read_text())
    commands = [
        shlex.split(terminal_args[index + 1])
        for index, arg in enumerate(terminal_args)
        if arg == "--command"
    ]
    assert len(commands) == 4
    gazebo_args, sitl_args, payload_args, relay_args = commands
    assert gazebo_args[-1] == str(gazebo / "worlds/cuadc2026_rescue.sdf")
    assert payload_args[-2] == "-lc"
    assert shlex.split(payload_args[-1]) == [
        "python3",
        str(gazebo / "tools/cuadc2026/payload_release_bridge.py"),
        "--config",
        str(gazebo / "config/cuadc2026-payload-release.yaml"),
    ]
    # Run only the generated ArduPilot shell command against a harmless recorder.
    sitl_result = subprocess.run(
        ["bash", "-c", sitl_args[-1]],
        cwd=unrelated_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sitl_result.returncode == 0, sitl_result.stderr
    recorded = json.loads(sitl_log.read_text())
    assert recorded["cwd"] == str(ardupilot)
    args = recorded["argv"]
    assert (
        f"--add-param-file={gazebo / 'config/gazebo-cuadc2026-fixed-camera.parm'}"
        in args
    )
    use_dir = args[args.index("--use-dir") + 1]
    assert use_dir == str(repo / "runtime/sitl/iris-gimbal")
    assert Path(use_dir).is_dir()
    assert "--out=udp:127.0.0.1:14550" in args
    assert "--out=udp:127.0.0.1:14551" in args
    assert "host=127.0.0.2" in shlex.split(relay_args[-1])


@pytest.mark.parametrize("override_mode", ["relative", "absolute"])
def test_launcher_resolves_file_overrides_from_repo(
    tmp_path: Path, override_mode: str
) -> None:
    repo = tmp_path / "override repo's checkout"
    launcher = repo / "scripts/run_iris_gimbal_sitl.sh"
    launcher.parent.mkdir(parents=True)
    shutil.copyfile(LAUNCHER, launcher)
    fake_bin = tmp_path / "fake bin"
    terminal_log = tmp_path / "terminal.json"
    _executable(
        fake_bin / "gnome-terminal",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['TERMINAL_LOG']).write_text(json.dumps(sys.argv[1:]))\n",
    )
    overrides = {
        "GZ_PARAM_FILE": "fixtures/params one's.parm",
        "GZ_WORLD": "fixtures/world one's.sdf",
        "PAYLOAD_BRIDGE": "fixtures/bridge one's.py",
        "PAYLOAD_CONFIG": "fixtures/payload one's.yaml",
    }
    if override_mode == "absolute":
        overrides = {key: str(tmp_path / value) for key, value in overrides.items()}
    env = os.environ.copy()
    for variable in PATH_VARIABLES:
        env.pop(variable, None)
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}", TERMINAL_LOG=str(terminal_log), **overrides
    )
    result = subprocess.run(
        ["bash", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    args = json.loads(terminal_log.read_text())
    commands = [
        shlex.split(args[index + 1])
        for index, arg in enumerate(args)
        if arg == "--command"
    ]
    assert commands[0][-1] == str(repo / overrides["GZ_WORLD"])
    sitl_command = shlex.split(commands[1][-1])
    assert f"--add-param-file={repo / overrides['GZ_PARAM_FILE']}" in sitl_command
    assert shlex.split(commands[2][-1]) == [
        "python3",
        str(repo / overrides["PAYLOAD_BRIDGE"]),
        "--config",
        str(repo / overrides["PAYLOAD_CONFIG"]),
    ]
