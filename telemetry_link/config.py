from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "telemetry.yaml"
RETIRED_CONFIG_FIELDS = frozenset({
    "legacy_writer_enabled", "v2_writer_enabled", "ui_enabled",
    "state_udp_enabled", "state_udp_ip", "state_udp_port",
    "control_send_rate_hz", "action_cmd_retries", "action_retry_interval_sec",
})


@dataclass(slots=True)
class EndpointConfig:
    name: str
    connection_type: str
    serial_port: str
    baudrate: int
    udp_mode: str
    udp_host: str
    udp_port: int
    tcp_host: str
    tcp_port: int
    eth_mode: str
    eth_host: str
    eth_port: int


@dataclass(slots=True)
class TelemetryConfig:
    data_source: str
    active_source: str
    sitl: EndpointConfig
    real: EndpointConfig
    heartbeat_timeout_sec: float
    rx_timeout_sec: float
    reconnect_interval_sec: float
    receiver_idle_sleep_sec: float
    sender_idle_sleep_sec: float
    request_message_intervals: bool
    message_interval_hz: dict[str, float]
    gimbal_mount_mode: int
    gimbal_yaw_min_deg: float
    gimbal_yaw_max_deg: float
    gimbal_pitch_min_deg: float
    gimbal_pitch_max_deg: float
    log_level: str
    ack_quarantine_ms: int = 250
    attitude_udp_enabled: bool = False
    attitude_udp_ip: str = "127.0.0.1"
    attitude_udp_port: int = 5011
    attitude_udp_rate_hz: float = 30.0


def _require_yaml_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{key} must be a YAML bool: use true or false without quotes")


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config yaml must be a mapping")
    retired = RETIRED_CONFIG_FIELDS.intersection(data)
    if retired:
        raise ValueError(f"retired telemetry config field(s): {', '.join(sorted(retired))}")
    allowed = set(TelemetryConfig.__dataclass_fields__) - {"sitl", "real"} | {"sitl", "real"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown telemetry config field(s): {', '.join(sorted(unknown))}")
    endpoint_allowed = set(EndpointConfig.__dataclass_fields__) - {"name"}
    for name in ("sitl", "real"):
        value = data.get(name)
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be a mapping")
        nested_unknown = set(value) - endpoint_allowed
        if nested_unknown:
            raise ValueError(f"unknown {name} field(s): {', '.join(sorted(nested_unknown))}")
    return data


def _build_endpoint(name: str, data: dict[str, Any]) -> EndpointConfig:
    return EndpointConfig(
        name=name,
        connection_type=str(data["connection_type"]),
        serial_port=str(data.get("serial_port", "/dev/ttyUSB0")),
        baudrate=int(data.get("baudrate", 115200)),
        udp_mode=str(data.get("udp_mode", "udpin")),
        udp_host=str(data.get("udp_host", "0.0.0.0")),
        udp_port=int(data.get("udp_port", 14550)),
        tcp_host=str(data.get("tcp_host", "127.0.0.1")),
        tcp_port=int(data.get("tcp_port", 5760)),
        eth_mode=str(data.get("eth_mode", "udpin")),
        eth_host=str(data.get("eth_host", "0.0.0.0")),
        eth_port=int(data.get("eth_port", 14550)),
    )


def _build_config(merged: dict[str, Any]) -> TelemetryConfig:
    retired = RETIRED_CONFIG_FIELDS.intersection(merged)
    if retired:
        raise ValueError(f"retired telemetry config field(s): {', '.join(sorted(retired))}")
    attitude_udp_ip = str(merged.get("attitude_udp_ip", "127.0.0.1"))
    if attitude_udp_ip not in {"127.0.0.1", "localhost"}:
        raise ValueError("attitude_udp_ip must be localhost-only")
    attitude_udp_port = int(merged.get("attitude_udp_port", 5011))
    if not 1 <= attitude_udp_port <= 65535:
        raise ValueError("attitude_udp_port must be in 1..65535")
    attitude_udp_enabled = _require_yaml_bool(
        merged.get("attitude_udp_enabled", False), "attitude_udp_enabled"
    )
    attitude_udp_rate_hz = float(merged.get("attitude_udp_rate_hz", 30.0))
    if not math.isfinite(attitude_udp_rate_hz) or attitude_udp_rate_hz <= 0.0:
        raise ValueError("attitude_udp_rate_hz must be finite and positive")
    if attitude_udp_enabled and attitude_udp_rate_hz < 30.0:
        raise ValueError("attitude_udp_rate_hz must be at least 30 when attitude UDP is enabled")
    return TelemetryConfig(
        data_source=str(merged["data_source"]),
        active_source=str(merged["active_source"]),
        sitl=_build_endpoint("sitl", dict(merged["sitl"])),
        real=_build_endpoint("real", dict(merged["real"])),
        heartbeat_timeout_sec=float(merged["heartbeat_timeout_sec"]),
        rx_timeout_sec=float(merged["rx_timeout_sec"]),
        reconnect_interval_sec=float(merged["reconnect_interval_sec"]),
        receiver_idle_sleep_sec=float(merged["receiver_idle_sleep_sec"]),
        sender_idle_sleep_sec=float(merged["sender_idle_sleep_sec"]),
        request_message_intervals=_require_yaml_bool(merged["request_message_intervals"], "request_message_intervals"),
        message_interval_hz={str(k): float(v) for k, v in dict(merged.get("message_interval_hz", {})).items()},
        gimbal_mount_mode=int(merged.get("gimbal_mount_mode", 2)),
        gimbal_yaw_min_deg=float(merged.get("gimbal_yaw_min_deg", -180.0)),
        gimbal_yaw_max_deg=float(merged.get("gimbal_yaw_max_deg", 180.0)),
        gimbal_pitch_min_deg=float(merged.get("gimbal_pitch_min_deg", -180.0)),
        gimbal_pitch_max_deg=float(merged.get("gimbal_pitch_max_deg", 180.0)),
        log_level=str(merged["log_level"]),
        ack_quarantine_ms=max(0, int(merged.get("ack_quarantine_ms", 250))),
        attitude_udp_enabled=attitude_udp_enabled,
        attitude_udp_ip=attitude_udp_ip,
        attitude_udp_port=attitude_udp_port,
        attitude_udp_rate_hz=attitude_udp_rate_hz,
    )


def load_config_file(path: str | Path) -> TelemetryConfig:
    return _build_config(_load_yaml(str(path)))


