from pathlib import Path


def test_healthcheck_discovers_environments_and_configured_model():
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "healthcheck"
        / "check_rk3588.sh"
    ).read_text(encoding="utf-8")

    assert '${APP_PYTHON:-}' in script
    assert '${YOLO_PYTHON:-}' in script
    assert '"${CONDA_BIN}" run -n' in script
    assert "rk3588" in script.lower()
    assert "/dev/rknpu" in script
    assert "data/models/cuadc-fp16.rknn" in script
    assert "anaconda3/envs" not in script
