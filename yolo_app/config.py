from __future__ import annotations

import argparse
import ipaddress
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class VirtualNadirAttitudeConfig:
    ip: str
    port: int
    source: str
    history_ms: float
    max_samples: int
    max_sample_distance_ms: float
    max_bracket_span_ms: float
    future_wait_ms: float
    min_rate_hz: float = 25.0
    rate_window_ms: float = 1000.0
    min_rate_samples: int = 4


@dataclass(frozen=True, slots=True)
class CameraCalibrationConfig:
    width: int
    height: int
    fx: float | None
    fy: float | None
    cx: float | None
    cy: float | None
    fov_x_deg: float | None
    fov_y_deg: float | None
    distortion: tuple[float, ...]
    r_body_camera: tuple[tuple[float, float, float], ...]
    approximate_calibration: bool


@dataclass(frozen=True, slots=True)
class VirtualOutputConfig:
    width: int
    height: int
    fx: float | None
    fy: float | None
    cx: float | None
    cy: float | None
    fov_x_deg: float | None
    fov_y_deg: float | None
    border_value: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class VirtualNadirConfig:
    enabled: bool
    yaw_reference_mode: str
    debug_compare: bool
    attitude: VirtualNadirAttitudeConfig
    camera: CameraCalibrationConfig
    output: VirtualOutputConfig


@dataclass(slots=True)
class AppConfig:
    model_path: str
    inference_workers: int
    source: str
    conf_thres: float
    iou_thres: float
    classes: list[int]
    udp_ip: str
    udp_port: int
    max_datagram_bytes: int
    max_detections: int
    selection_mode: str
    target_class: str
    max_lost_frames: int
    show: bool
    save_video: bool
    save_path: str
    line_width: int
    show_all_tracks: bool
    command_enabled: bool
    command_ip: str
    command_port: int
    window_name: str
    class_names: list[str]
    camera_width: int
    camera_height: int
    camera_fps: int
    camera_fourcc: str
    latest_frame: bool
    fullscreen: bool
    web_stream_enabled: bool
    web_stream_host: str
    web_stream_port: int
    web_stream_jpeg_quality: int
    web_stream_max_fps: float
    web_stream_width: int
    web_stream_height: int
    recording_dir: str
    virtual_nadir: VirtualNadirConfig


def _str_to_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def _strict_bool(value: Any, path: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{path} must be a YAML bool (true/false), got {value!r}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ground-side YOLO tracking app")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent.parent / "config" / "yolo.yaml"),
    )
    parser.add_argument("--model-path")
    parser.add_argument("--source")
    parser.add_argument("--conf-thres", type=float)
    parser.add_argument("--iou-thres", type=float)
    parser.add_argument("--classes", nargs="*", type=int)
    parser.add_argument("--udp-ip")
    parser.add_argument("--udp-port", type=int)
    parser.add_argument("--selection-mode", choices=["center", "biggest", "class"])
    parser.add_argument("--target-class")
    parser.add_argument("--max-lost-frames", type=int)
    parser.add_argument("--show", type=_str_to_bool)
    parser.add_argument("--save-video", type=_str_to_bool)
    parser.add_argument("--save-path")
    parser.add_argument("--line-width", type=int)
    parser.add_argument("--show-all-tracks", type=_str_to_bool)
    parser.add_argument("--command-enabled", type=_str_to_bool)
    parser.add_argument("--command-ip")
    parser.add_argument("--command-port", type=int)
    parser.add_argument("--window-name")
    parser.add_argument("--class-names", nargs="*")
    parser.add_argument("--camera-width", type=int)
    parser.add_argument("--camera-height", type=int)
    parser.add_argument("--camera-fps", type=int)
    parser.add_argument("--camera-fourcc")
    parser.add_argument("--latest-frame", type=_str_to_bool)
    parser.add_argument("--fullscreen", type=_str_to_bool)
    parser.add_argument("--recording-dir")
    return parser


def _load_yaml_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config yaml must be a mapping")
    allowed = {
        "model_path", "inference_workers", "source", "conf_thres", "iou_thres", "classes", "udp_ip", "udp_port",
        "max_datagram_bytes", "max_detections",
        "selection_mode", "target_class", "max_lost_frames", "show", "save_video", "save_path",
        "line_width", "show_all_tracks", "command_enabled", "command_ip", "command_port",
        "window_name", "class_names", "camera_width", "camera_height", "camera_fps", "camera_fourcc",
        "latest_frame", "display", "web_stream", "recording_dir", "virtual_nadir",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown YOLO config field(s): {', '.join(sorted(unknown))}")
    nested = {"display": {"local_window_enabled", "fullscreen"},
              "web_stream": {"enabled", "host", "port", "jpeg_quality", "max_fps", "width", "height"},
              "virtual_nadir": {"enabled", "yaw_reference_mode", "debug_compare", "attitude", "camera", "output"}}
    for name, keys in nested.items():
        value = data.get(name, {})
        if isinstance(value, dict) and set(value) - keys:
            raise ValueError(f"unknown {name} field(s): {', '.join(sorted(set(value) - keys))}")
    return data


def _optional_float(value: Any, path: str) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a number or null") from exc
    return result


def _virtual_nadir_config(data: Any) -> VirtualNadirConfig:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("virtual_nadir must be a mapping")
    attitude = data.get("attitude", {})
    camera = data.get("camera", {})
    output = data.get("output", {})
    if not all(isinstance(value, dict) for value in (attitude, camera, output)):
        raise ValueError("virtual_nadir attitude/camera/output must be mappings")
    allowed_attitude = {"ip", "port", "source", "history_ms", "max_samples",
                        "max_sample_distance_ms", "max_bracket_span_ms", "future_wait_ms",
                        "min_rate_hz", "rate_window_ms", "min_rate_samples"}
    allowed_camera = {"width", "height", "fx", "fy", "cx", "cy", "fov_x_deg",
                      "fov_y_deg", "distortion", "r_body_camera", "approximate_calibration"}
    allowed_output = {"width", "height", "fx", "fy", "cx", "cy", "fov_x_deg",
                      "fov_y_deg", "border_value"}
    for name, value, allowed_keys in (
        ("attitude", attitude, allowed_attitude),
        ("camera", camera, allowed_camera),
        ("output", output, allowed_output),
    ):
        unknown = set(value) - allowed_keys
        if unknown:
            raise ValueError(f"unknown virtual_nadir.{name} field(s): {', '.join(sorted(unknown))}")
    ip = str(attitude.get("ip", "127.0.0.1"))
    if ip not in {"127.0.0.1", "localhost"}:
        raise ValueError("virtual_nadir.attitude.ip must be localhost-only")
    source = str(attitude.get("source", "active"))
    if source not in {"active", "real", "sitl", "test"}:
        raise ValueError("virtual_nadir.attitude.source must be active, real, sitl, or test")
    port = int(attitude.get("port", 5011))
    history_ms = float(attitude.get("history_ms", 1500.0))
    max_samples = int(attitude.get("max_samples", 128))
    max_distance_ms = float(attitude.get("max_sample_distance_ms", 75.0))
    max_span_ms = float(attitude.get("max_bracket_span_ms", 150.0))
    future_wait_ms = float(attitude.get("future_wait_ms", 40.0))
    min_rate_hz = float(attitude.get("min_rate_hz", 25.0))
    rate_window_ms = float(attitude.get("rate_window_ms", 1000.0))
    min_rate_samples = int(attitude.get("min_rate_samples", 4))
    if not 1 <= port <= 65535 or history_ms <= 0 or max_samples < 2:
        raise ValueError("virtual_nadir attitude bounds are invalid")
    if max_distance_ms <= 0 or max_span_ms <= 0 or future_wait_ms < 0:
        raise ValueError("virtual_nadir attitude timing bounds are invalid")
    if (
        not math.isfinite(min_rate_hz)
        or not math.isfinite(rate_window_ms)
        or min_rate_hz <= 0
        or rate_window_ms <= 0
        or min_rate_samples < 3
    ):
        raise ValueError("virtual_nadir attitude rate monitor bounds are invalid")

    distortion = tuple(float(value) for value in camera.get("distortion", [0, 0, 0, 0, 0]))
    raw_rotation = camera.get(
        "r_body_camera", [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    )
    if (
        not isinstance(raw_rotation, list)
        or len(raw_rotation) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in raw_rotation)
    ):
        raise ValueError("virtual_nadir.camera.r_body_camera must be a 3x3 list")
    rotation = tuple(tuple(float(value) for value in row) for row in raw_rotation)
    raw_border = output.get("border_value", [0, 0, 0])
    if not isinstance(raw_border, list) or len(raw_border) != 3:
        raise ValueError("virtual_nadir.output.border_value must contain three integers")
    border = tuple(int(value) for value in raw_border)
    if any(value < 0 or value > 255 for value in border):
        raise ValueError("virtual_nadir.output.border_value entries must be in 0..255")
    yaw_mode = str(data.get("yaw_reference_mode", "lock_on_start"))
    if yaw_mode != "lock_on_start":
        raise ValueError("virtual_nadir V1 only supports yaw_reference_mode=lock_on_start")
    return VirtualNadirConfig(
        enabled=_strict_bool(data.get("enabled", False), "virtual_nadir.enabled"),
        yaw_reference_mode=yaw_mode,
        debug_compare=_strict_bool(data.get("debug_compare", False), "virtual_nadir.debug_compare"),
        attitude=VirtualNadirAttitudeConfig(
            ip, port, source, history_ms, max_samples, max_distance_ms, max_span_ms, future_wait_ms,
            min_rate_hz, rate_window_ms, min_rate_samples,
        ),
        camera=CameraCalibrationConfig(
            int(camera.get("width", 640)), int(camera.get("height", 480)),
            _optional_float(camera.get("fx"), "virtual_nadir.camera.fx"),
            _optional_float(camera.get("fy"), "virtual_nadir.camera.fy"),
            _optional_float(camera.get("cx"), "virtual_nadir.camera.cx"),
            _optional_float(camera.get("cy"), "virtual_nadir.camera.cy"),
            _optional_float(camera.get("fov_x_deg", 114.591559), "virtual_nadir.camera.fov_x_deg"),
            _optional_float(camera.get("fov_y_deg", 98.864783), "virtual_nadir.camera.fov_y_deg"),
            distortion, rotation,
            _strict_bool(camera.get("approximate_calibration", True),
                         "virtual_nadir.camera.approximate_calibration"),
        ),
        output=VirtualOutputConfig(
            int(output.get("width", 640)), int(output.get("height", 480)),
            _optional_float(output.get("fx"), "virtual_nadir.output.fx"),
            _optional_float(output.get("fy"), "virtual_nadir.output.fy"),
            _optional_float(output.get("cx"), "virtual_nadir.output.cx"),
            _optional_float(output.get("cy"), "virtual_nadir.output.cy"),
            _optional_float(output.get("fov_x_deg", 114.591559), "virtual_nadir.output.fov_x_deg"),
            _optional_float(output.get("fov_y_deg", 98.864783), "virtual_nadir.output.fov_y_deg"),
            border,
        ),
    )


def _expand_user_path(value: Any) -> str:
    text = str(value)
    if text.startswith("~"):
        return str(Path(text).expanduser())
    return text


def _resolve_config_path(value: Any, config_path: str) -> str:
    path = Path(_expand_user_path(value))
    if path.is_absolute():
        return str(path)
    return str((Path(config_path).resolve().parent / path).resolve())


def _resolve_video_source(value: Any, config_path: str) -> str:
    source = _expand_user_path(value)
    pipeline_markers = (
        " ! ", "appsink", "udpsrc", "rtspsrc", "rtph264depay",
        "avdec_h264", "videoconvert",
    )
    if (source.isdigit() or "://" in source
            or any(marker in source.lower() for marker in pipeline_markers)):
        return source
    return _resolve_config_path(source, config_path)


def load_config() -> AppConfig:
    parser = build_arg_parser()
    args = parser.parse_args()
    yaml_config = _load_yaml_config(args.config)
    display_config = yaml_config.get("display", {})
    web_stream_config = yaml_config.get("web_stream", {})
    if not isinstance(display_config, dict) or not isinstance(web_stream_config, dict):
        raise ValueError("display and web_stream config must be mappings")

    merged = dict(yaml_config)
    for key, value in vars(args).items():
        if key == "config":
            continue
        if value is not None:
            merged[key.replace("-", "_")] = value

    command_ip = str(merged.get("command_ip", "127.0.0.1"))
    try:
        command_is_loopback = ipaddress.ip_address(command_ip).is_loopback
    except ValueError:
        command_is_loopback = command_ip == "localhost"
    if _strict_bool(merged.get("command_enabled", True), "command_enabled") and not command_is_loopback:
        raise ValueError("command_ip must be loopback unless an authenticated transport is configured")
    max_datagram_bytes = int(merged.get("max_datagram_bytes", 60_000))
    max_detections = int(merged.get("max_detections", 128))
    if not 512 <= max_datagram_bytes <= 65_507 or not 1 <= max_detections <= 4096:
        raise ValueError("YOLO UDP bounds are invalid")
    inference_workers = int(merged.get("inference_workers", 1))
    if not 1 <= inference_workers <= 3:
        raise ValueError("inference_workers must be in 1..3 for RK3588")
    return AppConfig(
        model_path=_resolve_config_path(merged["model_path"], args.config),
        inference_workers=inference_workers,
        source=_resolve_video_source(merged["source"], args.config),
        conf_thres=float(merged["conf_thres"]),
        iou_thres=float(merged["iou_thres"]),
        classes=list(merged.get("classes", [])),
        udp_ip=str(merged["udp_ip"]),
        udp_port=int(merged["udp_port"]),
        max_datagram_bytes=max_datagram_bytes,
        max_detections=max_detections,
        selection_mode=str(merged["selection_mode"]),
        target_class=str(merged.get("target_class", "")),
        max_lost_frames=int(merged["max_lost_frames"]),
        show=_strict_bool(
            display_config.get("local_window_enabled", merged["show"]),
            "display.local_window_enabled",
        ),
        save_video=_strict_bool(merged["save_video"], "save_video"),
        save_path=_resolve_config_path(merged["save_path"], args.config),
        line_width=int(merged.get("line_width", 2)),
        show_all_tracks=_strict_bool(merged.get("show_all_tracks", True), "show_all_tracks"),
        command_enabled=_strict_bool(merged.get("command_enabled", True), "command_enabled"),
        command_ip=command_ip,
        command_port=int(merged.get("command_port", 5006)),
        window_name=str(merged.get("window_name", "YOLO Tracking")),
        class_names=list(merged.get("class_names", ["Target", "bucket", "class_2"])),
        camera_width=int(merged.get("camera_width", 640)),
        camera_height=int(merged.get("camera_height", 480)),
        camera_fps=int(merged.get("camera_fps", 30)),
        camera_fourcc=str(merged.get("camera_fourcc", "MJPG")),
        latest_frame=_strict_bool(merged.get("latest_frame", False), "latest_frame"),
        fullscreen=_strict_bool(
            display_config.get("fullscreen", merged.get("fullscreen", False)),
            "display.fullscreen",
        ),
        web_stream_enabled=_strict_bool(web_stream_config.get("enabled", False), "web_stream.enabled"),
        web_stream_host=str(web_stream_config.get("host", "0.0.0.0")),
        web_stream_port=int(web_stream_config.get("port", 8081)),
        web_stream_jpeg_quality=int(web_stream_config.get("jpeg_quality", 75)),
        web_stream_max_fps=float(web_stream_config.get("max_fps", 20.0)),
        web_stream_width=int(web_stream_config.get("width", 0)),
        web_stream_height=int(web_stream_config.get("height", 0)),
        recording_dir=_resolve_config_path(
            merged.get("recording_dir", "../runtime/videos"), args.config
        ),
        virtual_nadir=_virtual_nadir_config(merged.get("virtual_nadir")),
    )
