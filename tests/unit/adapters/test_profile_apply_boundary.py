import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_profile_apply_cannot_restore_missions_or_replace_current_templates(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "app.yaml").write_text("executor:\n  send_commands: false\n")
    (config / "telemetry.yaml").write_text("data_source: real\n")
    (config / "yolo.yaml").write_text("source: camera\n")
    scripts = tmp_path / "scripts" / "config"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/config/render_profile.py", scripts / "render_profile.py")

    templates = config / "action_missions"
    templates.mkdir()
    (templates / "current.json").write_text('{"current": true}\n')
    profile = config / "profiles" / "test"
    profile.mkdir(parents=True)
    (profile / "profile.yaml").write_text(
        "profile: test\noverrides:\n  telemetry:\n    data_source: sitl\nexecutor:\n  send_commands: false\n"
    )
    retired = profile / "missions" / "retired"
    retired.mkdir(parents=True)
    (retired / "config.yaml").write_text("legacy: true\n")
    copies = profile / "action_missions"
    copies.mkdir()
    (copies / "current.json").write_text('{"retired": true}\n')

    result = subprocess.run(
        ["bash", "-c", 'set -e; source "$1"; profile_require_send_commands_off "$2/config/app.yaml"; profile_apply "$2" "$3"',
         "profile-boundary-test", str(ROOT / "scripts/config/profile_common.sh"), str(tmp_path), str(profile)],
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load((config / "telemetry.yaml").read_text())["data_source"] == "sitl"
    assert (templates / "current.json").read_text() == '{"current": true}\n'
    assert not (tmp_path / "missions").exists()
