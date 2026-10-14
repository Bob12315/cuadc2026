from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from yolo_app.config import load_config


def _config() -> dict[str, object]:
    return {
        "model_path": "../data/models/model.rknn",
        "source": "/dev/video0",
        "conf_thres": 0.25,
        "iou_thres": 0.45,
        "classes": [],
        "udp_ip": "127.0.0.1",
        "udp_port": 5005,
        "selection_mode": "class",
        "target_class": "target",
        "max_lost_frames": 5,
        "show": False,
        "save_video": False,
        "save_path": "../runtime/videos/output.mp4",
        "line_width": 3,
        "show_all_tracks": True,
        "command_enabled": True,
        "command_ip": "127.0.0.1",
        "command_port": 5006,
        "window_name": "YOLO Tracking",
        "class_names": ["target"],
        "camera_width": 640,
        "camera_height": 480,
        "camera_fps": 30,
        "camera_fourcc": "MJPG",
        "latest_frame": True,
        "display": {"local_window_enabled": False, "fullscreen": False},
        "web_stream": {"enabled": True, "host": "0.0.0.0", "port": 8081},
        "recording_dir": "~/uav_recordings",
    }


def _write_config(tmp_path: Path, data: dict[str, object]) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "yolo.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_load_config_resolves_paths_relative_to_yaml(tmp_path: Path, monkeypatch) -> None:
    path = _write_config(tmp_path, _config())
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    cfg = load_config()

    assert cfg.model_path == str(tmp_path / "data" / "models" / "model.rknn")
    assert cfg.inference_workers == 1
    assert cfg.save_path == str(tmp_path / "runtime" / "videos" / "output.mp4")
    assert cfg.latest_frame is True
    assert cfg.web_stream_enabled is True
    assert cfg.web_stream_width == 0
    assert cfg.web_stream_height == 0
    assert Path(cfg.recording_dir).name == "uav_recordings"


@pytest.mark.parametrize("recording_dir", [None, "../runtime/videos"])
def test_recording_directory_is_relative_to_config_not_launch_directory(
    tmp_path: Path, monkeypatch, recording_dir: str | None,
) -> None:
    data = _config()
    if recording_dir is None:
        data.pop("recording_dir")
    else:
        data["recording_dir"] = recording_dir
    path = _write_config(tmp_path, data)
    launch_dir = tmp_path / "other" / "launch"
    launch_dir.mkdir(parents=True)
    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    assert load_config().recording_dir == str(tmp_path / "runtime" / "videos")


def test_local_video_file_is_relative_to_config_not_launch_directory(
    tmp_path: Path, monkeypatch,
) -> None:
    data = _config()
    data["source"] = "../runtime/videos/input clip.mp4"
    path = _write_config(tmp_path, data)
    launch_dir = tmp_path / "other"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    assert load_config().source == str(tmp_path / "runtime" / "videos" / "input clip.mp4")


@pytest.mark.parametrize("source", [
    "5600", "/dev/video0", "/dev/v4l/by-id/camera-video-index0",
    "rtsp://camera.local/live", "file:///external/input.mp4",
    "videotestsrc ! videoconvert ! appsink",
])
def test_video_source_paths_preserve_devices_ports_streams_and_pipelines(
    tmp_path: Path, monkeypatch, source: str,
) -> None:
    data = _config()
    data["source"] = source
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    assert load_config().source == source


def test_load_config_accepts_three_rknn_inference_workers(tmp_path: Path, monkeypatch) -> None:
    data = _config()
    data["inference_workers"] = 3
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    assert load_config().inference_workers == 3


def test_load_config_rejects_more_than_three_inference_workers(
    tmp_path: Path, monkeypatch
) -> None:
    data = _config()
    data["inference_workers"] = 4
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    with pytest.raises(ValueError, match="inference_workers must be in 1..3"):
        load_config()


def test_load_config_reads_web_stream_dimensions(tmp_path: Path, monkeypatch) -> None:
    data = _config()
    web_stream = data["web_stream"]
    assert isinstance(web_stream, dict)
    web_stream["width"] = 480
    web_stream["height"] = 360
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    cfg = load_config()

    assert cfg.web_stream_width == 480
    assert cfg.web_stream_height == 360


def test_load_config_rejects_quoted_false_bool(tmp_path: Path, monkeypatch) -> None:
    data = _config()
    data["save_video"] = "false"
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    with pytest.raises(ValueError, match="save_video must be a YAML bool"):
        load_config()


def test_cli_bool_override_is_parsed(tmp_path: Path, monkeypatch) -> None:
    data = _config()
    data["save_video"] = True
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path), "--save-video", "false"])

    cfg = load_config()

    assert cfg.save_video is False


def test_virtual_nadir_defaults_disabled_with_approximate_calibration(
    tmp_path: Path, monkeypatch
) -> None:
    path = _write_config(tmp_path, _config())
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    cfg = load_config()

    assert cfg.virtual_nadir.enabled is False
    assert cfg.virtual_nadir.yaw_reference_mode == "lock_on_start"
    assert cfg.virtual_nadir.attitude.source == "active"
    assert cfg.virtual_nadir.attitude.min_rate_hz == 25.0
    assert cfg.virtual_nadir.camera.approximate_calibration is True
    assert cfg.virtual_nadir.camera.r_body_camera == (
        (0.0, -1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )


def test_virtual_nadir_reads_explicit_camera_and_output_k(tmp_path: Path, monkeypatch) -> None:
    data = _config()
    data["virtual_nadir"] = {
        "enabled": True,
        "yaw_reference_mode": "lock_on_start",
        "debug_compare": True,
        "attitude": {"source": "test", "future_wait_ms": 0},
        "camera": {
            "width": 640,
            "height": 480,
            "fx": 300,
            "fy": 301,
            "cx": 320,
            "cy": 240,
            "distortion": [0.1, -0.01, 0, 0, 0],
            "r_body_camera": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
            "approximate_calibration": False,
        },
        "output": {
            "width": 640,
            "height": 480,
            "fx": 280,
            "fy": 281,
            "cx": 320,
            "cy": 240,
        },
    }
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    cfg = load_config()

    assert cfg.virtual_nadir.enabled is True
    assert cfg.virtual_nadir.debug_compare is True
    assert cfg.virtual_nadir.attitude.source == "test"
    assert cfg.virtual_nadir.attitude.min_rate_samples == 4
    assert cfg.virtual_nadir.camera.fx == 300.0
    assert cfg.virtual_nadir.camera.distortion[0] == pytest.approx(0.1)
    assert cfg.virtual_nadir.output.fy == 281.0


def test_virtual_nadir_rejects_non_localhost_attitude(tmp_path: Path, monkeypatch) -> None:
    data = _config()
    data["virtual_nadir"] = {"enabled": True, "attitude": {"ip": "0.0.0.0"}}
    path = _write_config(tmp_path, data)
    monkeypatch.setattr("sys.argv", ["yolo", "--config", str(path)])

    with pytest.raises(ValueError, match="localhost-only"):
        load_config()
