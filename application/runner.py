from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from types import SimpleNamespace
from typing import Mapping

from app.config import AppConfig, ROOT_DIR, load_telemetry_config
from execution.dispatcher import ActionDispatcher
from application.vehicle_projection import project_legacy_vehicle_frame
from application.command_observer import CommandCompletionObserver
from application.action_runtime import ActionRuntimeService
from observability.blackbox import BlackboxRecorder
from observability.event_publisher import IsolatedEventPublisher, LegacyRecentEventSink
from contracts.platform.common import SchemaVersion
from contracts.platform.observability import OperationalEvent
from contracts.platform.field import GpsObservation
from field.context import RuntimeContextBuilder
from field.service import FieldService
from field.profile_service import ReadOnlyFieldProfileRepository
from web_ui.status_adapter import WebStatusService
from app.bootstrap import ServiceManager
from missions.common.actions.action_lab import action_lab_specs, create_action_lab_registry
from missions.common.actions.runner import ActionRunner
from application.send_state import SystemSendState

from application.yolo_command_client import YoloCommandClient
from application.state_store import ApplicationStateStore
from application.result_service import ResultService
from application.system_control import SystemControlService
from application.mission_service import MissionApplicationService


@dataclass(frozen=True, slots=True)
class _IdleCommandSnapshot:
    """Neutral blackbox/status value for the current Action-only runtime."""

    vx_cmd: float = 0.0
    vy_cmd: float = 0.0
    vz_cmd: float = 0.0
    yaw_rate_cmd: float = 0.0
    gimbal_yaw_rate_cmd: float = 0.0
    gimbal_pitch_rate_cmd: float = 0.0
    gimbal_yaw_angle_cmd: float = 0.0
    gimbal_pitch_angle_cmd: float = 0.0
    enable_body: bool = False
    enable_gimbal: bool = False
    enable_gimbal_angle: bool = False
    enable_approach: bool = False
    active: bool = False
    valid: bool = True


class SystemRunner:
    _RESULT_ATTRIBUTES = {
        "latest_localization_result": "localization",
        "latest_drop_localization_result": "drop_localization",
        "latest_recon_localization_result": "recon_localization",
        "latest_drop_targets_result": "drop_targets",
        "latest_drop_workflow_result": "drop_workflow",
    }

    def __getattr__(self, name: str):
        result_name = self._RESULT_ATTRIBUTES.get(name)
        if result_name is not None and "result_service" in self.__dict__:
            return self.result_service.edit_view(result_name)
        result_service = self.__dict__.get("result_service")
        if result_service is not None:
            delegated = getattr(result_service, name, None)
            if callable(delegated):
                return delegated
        mission_service = self.__dict__.get("mission_service")
        if mission_service is not None:
            delegated = getattr(mission_service, name, None)
            if callable(delegated) or name == "action_mission_orchestrator":
                return delegated
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        result_name = self._RESULT_ATTRIBUTES.get(name)
        if result_name is not None and "result_service" in self.__dict__:
            self.result_service.set(result_name, value)
            return
        if name == "action_mission_orchestrator" and "mission_service" in self.__dict__:
            self.mission_service.action_mission_orchestrator = value
            return
        object.__setattr__(self, name, value)

    def __init__(self, config: AppConfig, stop_event: threading.Event | None = None) -> None:
        self.config = config
        self.stop_event = stop_event or threading.Event()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.services = ServiceManager(config, self.stop_event)
        self.mission_enabled = False
        self.blackbox = BlackboxRecorder(config.blackbox)
        self.controller_switches = SystemSendState(config.start_send_commands)
        self.control_command_log: deque[str] = deque(maxlen=120)
        self.control_command_log_lock = threading.Lock()
        self.runtime_config_lock = threading.RLock()
        self.latest_mission_name = "action_lab_only"
        self.latest_mission_stage = "NO_MISSION"
        self.latest_stage_controller = "NO_MISSION"
        self.latest_hold_reason = "mission_disabled"
        self.last_send_commands: bool | None = None
        self.web_server = None
        self.system_events: deque[dict[str, object]] = deque(maxlen=160)
        self.event_publisher = IsolatedEventPublisher((
            ("recent", LegacyRecentEventSink(self.system_events), 160),
        ))
        self.state_store = ApplicationStateStore()
        self.result_service = ResultService(
            with_field_coordinates=self._with_field_coordinates,
            get_action_runtime=lambda: getattr(self, "action_runtime", None),
            get_mission_orchestrator=lambda: self.mission_service.action_mission_orchestrator,
            record_event=self._record_event,
        )
        self.action_lab_specs = action_lab_specs()
        self.action_lab_enabled = True
        self.action_runtime_lock = threading.RLock()
        self.runtime_context_builder = RuntimeContextBuilder(logger=self.logger)
        self.field_profile_repository = ReadOnlyFieldProfileRepository((
            ("config", ROOT_DIR / "config" / "field_profiles"),
            ("runtime", ROOT_DIR / "runtime" / "field_profiles"),
        ))
        self.field_service = FieldService(
            runtime_context_builder=self.runtime_context_builder,
            profile_repository=self.field_profile_repository,
        )
        self.services.set_field_reference_version_port(self.field_service._svc)
        self.field_service._svc.subscribe_version_change(
            lambda _version, reason: self.services.command_port.cancel_stale_field_commands(reason)
        )
        self.web_status_service = WebStatusService(
            runtime_context_builder=self.runtime_context_builder,
            get_snapshot=self.state_store.read,
            lock=self.control_command_log_lock,
            controller_switches=self.controller_switches,
            control_command_log=self.control_command_log,
            system_events=self.system_events,
            action_lab_enabled=self.action_lab_enabled,
            action_lab_specs=self.action_lab_specs,
            get_action_mission_status_payload=lambda: self.mission_service.action_mission_status_payload(),
            get_link_manager=lambda: self.services.state_port,
            get_action_runtime=lambda: self.action_runtime,
            latest_mission_name=self.latest_mission_name,
            latest_mission_stage=self.latest_mission_stage,
            latest_stage_controller=self.latest_stage_controller,
            latest_hold_reason=self.latest_hold_reason,
            get_latest_localization_result=lambda: self.result_service.get("localization"),
            get_latest_drop_localization_result=lambda: self.result_service.get("drop_localization"),
            get_latest_recon_localization_result=lambda: self.result_service.get("recon_localization"),
            get_latest_drop_targets_result=lambda: self.result_service.get("drop_targets"),
            get_latest_drop_workflow_result=lambda: self.result_service.get("drop_workflow"),
        )
        self.action_runtime = ActionRuntimeService(
            runner=ActionRunner(create_action_lab_registry()),
            dispatcher=ActionDispatcher(
                logger=self.logger,
                yolo_client=YoloCommandClient(
                    self.config.yolo_command,
                    self.services.get_yolo_process_session_id,
                ),
                state_port=self.services.state_port,
                command_port=self.services.command_port,
            )
        )
        self.command_observer = CommandCompletionObserver(
            self.services.command_port.update_completion
        )
        self.system_control = SystemControlService(
            send_state=self.controller_switches,
            get_link=lambda: self.services.link_control,
            action_runtime=self.action_runtime,
            yolo_command_config=self.config.yolo_command,
            restart_commands={"app": self.config.services_control.restart_app_command,
                              "yolo": self.config.services_control.restart_yolo_command},
            record_event=self._record_event,
            reconnect_telemetry=lambda: self.services.reconnect_telemetry(
                load_telemetry_config(str(ROOT_DIR / "config" / "telemetry.yaml"))
            ),
            get_perception_snapshot=self.services.get_perception_platform_snapshot,
            get_yolo_session_id=self.services.get_yolo_process_session_id,
            video_dir=ROOT_DIR / "runtime" / "videos",
        )
        self.mission_service = MissionApplicationService(self)

    def run(self) -> None:
        self.services.start()
        self.blackbox.start()
        if self.config.ui.web_enabled:
            from web_ui.server import WebUiServer

            from application.web_services import WebServices

            self.web_server = WebUiServer(WebServices.from_runner(self), self.config.ui)
            self.web_server.start()
        try:
            self._action_lab_only_loop()
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        self.mission_service._stop_action_mission_recording(trigger="app_shutdown")
        self.blackbox.close()
        self.event_publisher.close(timeout_s=1.0)
        if self.web_server is not None:
            self.web_server.stop()
            self.web_server = None
        self.services.stop()
        self.system_control.stop()
        self.logger.info("app runtime stopped")

    def _action_lab_only_loop(self) -> None:
        loop_sleep_sec = 1.0 / max(self.config.runtime.loop_hz, 0.1)
        print_sleep_sec = 1.0 / max(self.config.runtime.print_rate_hz, 0.1)
        started_at = time.time()
        last_print_time = 0.0

        try:
            while not self.stop_event.is_set():
                self.mission_service._tick_action_mission_in_background()
                now = time.time()
                run_seconds = self.config.runtime.run_seconds
                if run_seconds is not None and (now - started_at) >= run_seconds:
                    self.stop_event.set()
                    break

                perception, scene = self.services.get_perception_frame(now)
                perception_status = self.services.perception_status(now)
                vehicle_snapshot = self.services.get_vehicle_snapshot()
                for envelope, command_status in self.services.command_port.observation_candidates():
                    self.command_observer.observe(envelope, command_status, vehicle_snapshot)
                drone, gimbal, link = project_legacy_vehicle_frame(vehicle_snapshot)
                self._observe_runtime_field_sampling(vehicle_snapshot, now_s=now)
                fused = self.services.fusion_manager.update(perception, drone, gimbal)
                command = _IdleCommandSnapshot()
                inputs = SimpleNamespace(
                    dt=loop_sleep_sec,
                    target_valid=bool(getattr(perception, "target_valid", False)),
                    target_locked=str(getattr(perception, "tracking_state", "")).lower() == "locked",
                    control_allowed=bool(getattr(drone, "control_allowed", False)),
                )
                mission = SimpleNamespace(
                    active_mode="action_lab_only",
                    hold_reason="mission_disabled",
                )
                mode_status = SimpleNamespace(
                    mode_name="action_lab_only",
                    hold_reason="mission_disabled",
                )
                health = SimpleNamespace(hold_reason="mission_disabled")

                field_reference_status = self.field_service.status()
                field_reference = (
                    field_reference_status.get("field_reference", {})
                    if isinstance(field_reference_status, dict)
                    else {}
                )

                with self.control_command_log_lock:
                    self.latest_mission_name = "action_lab_only"
                    self.latest_mission_stage = "NO_MISSION"
                    self.latest_stage_controller = "NO_MISSION"
                    self.latest_hold_reason = "mission_disabled"
                    runtime_snapshot = {
                        "perception": asdict(perception),
                        "perception_status": perception_status,
                        "scene": asdict(scene),
                        "drone": asdict(drone),
                        "gimbal": asdict(gimbal),
                        "link": asdict(link) if link is not None else {},
                        "health": {"hold_reason": "mission_disabled"},
                        "command": asdict(command),
                        "mission_detail": {
                            "enabled": False,
                            "name": "action_lab_only",
                            "reason": "mission_modules_unavailable",
                        },
                        "field_reference": field_reference,
                    }
                    self.state_store.replace(runtime_snapshot, updated_at=now)

                self._record_blackbox_cycle(
                    now=now,
                    dt=loop_sleep_sec,
                    perception=perception,
                    scene=scene,
                    drone=drone,
                    gimbal=gimbal,
                    link=link,
                    fused=fused,
                    inputs=inputs,
                    mission=mission,
                    health=health,
                    mode_status=mode_status,
                    raw_command=command,
                    shaped_command=command,
                    send_commands=False,
                )

                if (now - last_print_time) >= print_sleep_sec:
                    self.logger.info(
                        "mode=action_lab_only mission disabled; web UI active; SEND=OFF"
                    )
                    last_print_time = now

                time.sleep(loop_sleep_sec)
        except Exception:
            self.logger.exception("app action-lab-only loop failed")
            self.stop_event.set()

    def _record_blackbox_cycle(
        self,
        *,
        now: float,
        dt: float,
        perception,
        scene,
        drone,
        gimbal,
        link,
        fused,
        inputs,
        mission,
        health,
        mode_status,
        raw_command: _IdleCommandSnapshot,
        shaped_command: _IdleCommandSnapshot,
        send_commands: bool,
    ) -> None:
        armed = bool(getattr(drone, "armed", False))
        if not self.blackbox.update_recording_state(armed=armed, now=now):
            return
        self.blackbox.record(
            now=now,
            dt=dt,
            perception=perception,
            scene=scene,
            drone=drone,
            gimbal=gimbal,
            link=link,
            fused=fused,
            inputs=inputs,
            mission=mission,
            health=health,
            mode_status=mode_status,
            raw_command=raw_command,
            shaped_command=shaped_command,
            send_commands=send_commands,
            debug=self._blackbox_debug_payload(
                raw_command=raw_command,
                shaped_command=shaped_command,
                send_commands=send_commands,
            ),
        )

    def _blackbox_debug_payload(
        self,
        *,
        raw_command: _IdleCommandSnapshot,
        shaped_command: _IdleCommandSnapshot,
        send_commands: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "commands": {
                "raw": asdict(raw_command),
                "shaped": asdict(shaped_command),
                "send_commands": bool(send_commands),
            }
        }
        runtime = getattr(self, "action_runtime", None)
        if runtime is None:
            return payload

        last_result = runtime.last_result or {}
        dispatcher = runtime.dispatcher
        action_name = runtime.action_name
        action_payload: dict[str, object] = {
            "name": action_name,
            "state": runtime.runner.state,
            "run_id": dispatcher.authorization.run_id if dispatcher.authorization else None,
            "run_authorized": dispatcher.authorization is not None,
            "last_result": last_result,
            "last_dispatch": getattr(dispatcher, "last_dispatch", {}),
        }
        payload["action_lab"] = action_payload

        if action_name == "align_descend" or _result_name_is_align_descend(last_result):
            detail = last_result.get("detail") if isinstance(last_result, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            payload["align_descend"] = {
                "state": runtime.runner.state,
                "reason": last_result.get("reason", "") if isinstance(last_result, dict) else "",
                "done": bool(last_result.get("done", False)) if isinstance(last_result, dict) else False,
                "failed": bool(last_result.get("failed", False)) if isinstance(last_result, dict) else False,
                "detail": detail,
                "command": detail.get("command", {}),
                "last_dispatch": getattr(dispatcher, "last_dispatch", {}),
            }
        return payload

    def disable_automatic_sending(self, reason: str) -> None:
        self.system_control.disable_send(reason)

    def _record_event(self, level: str, message: str) -> None:
        runtime = getattr(self, "action_runtime", None)
        authorization = None if runtime is None else runtime.dispatcher.authorization
        event = OperationalEvent(
            SchemaVersion(1, 0), uuid.uuid4().hex, datetime.now(timezone.utc), time.monotonic_ns(),
            "application", "system_event", str(level), str(level).lower(),
            None if authorization is None else authorization.run_id, None,
            self.active_telemetry_source(), SchemaVersion(1, 0), MappingProxyType({"message": str(message)}),
        )
        self.event_publisher.publish(event)

    def web_status_snapshot(self) -> dict[str, object]:
        return self.web_status_service.snapshot()

    def action_lab_context(self) -> dict[str, object]:
        snapshot = self.state_store.read()
        return self.runtime_context_builder.build_action_context(snapshot)

    # ------------------------------------------------------------------
    # Field Reference API handlers — schema-v3 runtime GPS only
    # ------------------------------------------------------------------

    def _drone_snapshot_for_controller(self) -> dict[str, object]:
        snapshot = self.state_store.read()
        return snapshot.get("drone", {}) or {}

    def field_reference_status(self) -> dict[str, object]:
        with self.action_runtime_lock:
            result = dict(self.field_service.status())
            drone = self._drone_snapshot_for_controller()
            result["telemetry"] = {
                "global_position_valid": bool(drone.get("global_position_valid", False)),
                "lat": drone.get("lat"), "lon": drone.get("lon"),
                "last_global_position_time": drone.get("last_global_position_time"),
                "gps_fix_type": drone.get("gps_fix_type", 0),
                "satellites_visible": drone.get("satellites_visible", 0),
                "gps_eph": drone.get("gps_eph", -1.0), "gps_epv": drone.get("gps_epv", -1.0),
            }
            return result

    def field_reference_reset(self) -> dict[str, object]:
        with self.action_runtime_lock:
            return self.field_service.reset()

    def field_reference_freeze(self) -> dict[str, object]:
        with self.action_runtime_lock:
            return self.field_service.freeze()

    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # Field Profile API handlers (Phase C-1)
    # ------------------------------------------------------------------

    def field_profile_list(self) -> dict[str, object]:
        profiles = [{"profile_id": item.profile_id, "name": item.name, "source": item.source,
                     "schema_version": item.schema.major, "valid": item.valid,
                     "errors": list(item.errors), "warnings": list(item.warnings),
                     "content_sha256": item.content_sha256, "template_only": item.template_only}
                    for item in self.field_profile_repository.list()]
        return {"ok": True, "profiles": profiles}

    def field_profile_get(self, profile_id: str) -> dict[str, object]:
        try:
                p = self.field_profile_repository.load_profile(profile_id)
                sv = p.schema_version
                resp: dict[str, object] = {
                    "ok": True,
                    "profile_id": p.profile_id,
                    "name": p.name,
                    "schema_version": sv,
                    "coordinate_convention": p.coordinate_convention,
                    "gps_quality": {
                        "min_fix_type": p.gps_quality.min_fix_type,
                        "min_satellites": p.gps_quality.min_satellites,
                        "max_eph": p.gps_quality.max_eph,
                        "max_epv": p.gps_quality.max_epv,
                    },
                    "field_geometry": {
                        "lane_half_width_m": p.field_geometry.lane_half_width_m,
                        "drop_center_y_m": p.field_geometry.drop_center_y_m,
                        "recce_center_y_m": p.field_geometry.recce_center_y_m,
                        "drop_area_y_min": p.field_geometry.drop_area_y_min,
                        "drop_area_y_max": p.field_geometry.drop_area_y_max,
                        "recce_area_y_min": p.field_geometry.recce_area_y_min,
                        "recce_area_y_max": p.field_geometry.recce_area_y_max,
                    },
                    "binding_policy": {"min_baseline_m": p.binding_policy.min_baseline_m, "warn_baseline_below_m": p.binding_policy.warn_baseline_below_m},
                }
                if sv == 3:
                    fm = p.forward_marker
                    resp["forward_marker"] = {"name": fm.name, "lat": fm.lat, "lon": fm.lon, "coordinate_system": fm.coordinate_system} if fm else None
                    ds = p.drop_scan
                    if ds:
                        resp["drop_scan"] = {"waypoints": [
                            {"name": f"DROP_SCAN_{i+1}", "x_m": wp.x_m, "y_m": wp.y_m, "altitude_m": wp.altitude_m}
                            for i, wp in enumerate(ds.waypoints)
                        ]}
                    else:
                        resp["drop_scan"] = None
                    ros = p.runtime_origin_sampling
                    resp["runtime_origin_sampling"] = {"min_samples": ros.min_samples, "sample_window_s": ros.sample_window_s,
                                                         "max_horizontal_spread_m": ros.max_horizontal_spread_m, "estimator": ros.estimator} if ros else None
                return resp
        except Exception as exc:
            return self._field_profile_error(profile_id, str(exc))

    def field_profile_validate(self, profile_id: str) -> dict[str, object]:
        try:
            record = self.field_profile_repository.get(profile_id)
            return {"ok": record.valid, "profile_id": record.profile_id,
                    "errors": list(record.errors), "warnings": list(record.warnings)}
        except Exception as exc:
            return self._field_profile_error(profile_id, str(exc))


    def _observe_runtime_field_sampling(
        self,
        vehicle_snapshot: object,
        *,
        now_s: float,
    ) -> None:
        try:
            snapshot = vehicle_snapshot
            source_time = getattr(snapshot, "global_position_received_at_utc_s", None)
            with self.action_runtime_lock:
                result = self.field_service.observe_runtime_profile_sampling(GpsObservation(
                    observation_id=f"vehicle:{source_time}",
                    observed_at_s=now_s,
                    global_position_valid=bool(getattr(snapshot, "global_valid", False)),
                    lat=self._float_or_none(getattr(snapshot, "latitude_deg", None)),
                    lon=self._float_or_none(getattr(snapshot, "longitude_deg", None)),
                    gps_fix_type=int(getattr(snapshot, "gps_fix_type", 0) or 0),
                    satellites_visible=int(getattr(snapshot, "satellites_visible", 0) or 0),
                    gps_eph=self._float_or_none(getattr(snapshot, "gps_eph_m", None)) or -1.0,
                    gps_epv=self._float_or_none(getattr(snapshot, "gps_epv_m", None)) or -1.0,
                    last_global_position_time=self._float_or_none(source_time),
                ))
            if result.get("auto_finalized") is True:
                if result.get("ok") is True:
                    self._record_event(
                        "FIELD_REFERENCE",
                        "GPS sampling passed; field reference automatically confirmed and frozen",
                    )
                else:
                    self._record_event(
                        "FIELD_REFERENCE",
                        "GPS sampling auto-confirm/freeze failed: "
                        f"{result.get('error', 'unknown error')}",
                    )
        except Exception:
            self.logger.warning("runtime field sampling observe failed", exc_info=True)

    def field_profile_runtime_sampling_start(self, profile_id):
        with self.action_runtime_lock:
            return self.field_service.start_runtime_profile_sampling(profile_id, started_at_s=time.time())

    def field_profile_runtime_sampling_finalize(self):
        with self.action_runtime_lock:
            return self.field_service.finalize_runtime_profile_binding(completed_at_s=time.time())

    def field_profile_runtime_sampling_cancel(self):
        with self.action_runtime_lock:
            return self.field_service.cancel_runtime_profile_sampling()

    def competition_runtime_sampling_start(
        self, forward_marker_lat: float, forward_marker_lon: float
    ) -> dict[str, object]:
        with self.action_runtime_lock:
            return self.field_service.start_competition_runtime_sampling(
                forward_marker_lat=forward_marker_lat,
                forward_marker_lon=forward_marker_lon,
                started_at_s=time.time(),
            )

    @staticmethod
    def _field_profile_error(profile_id: str, error: str) -> dict[str, object]:
        return {
            "ok": False,
            "error": error,
            "profile_id": profile_id,
            "errors": [error],
            "warnings": [],
            "diagnostics": {"errors": [error], "warnings": []},
        }

    def _with_field_coordinates(self, items: list[object]) -> list[object]:
        enriched: list[object] = []
        for item in items:
            if not isinstance(item, dict):
                enriched.append(item)
                continue
            copy = dict(item)

            # P1: already has valid field coordinates — keep as-is
            fx = self._float_or_none(copy.get("field_x"))
            fy = self._float_or_none(copy.get("field_y"))
            if fx is not None and fy is not None:
                enriched.append(copy)
                continue

            # P2: GPS-first object — convert lat/lon → FIELD via runtime GPS reference
            lat = self._float_or_none(copy.get("lat"))
            lon = self._float_or_none(copy.get("lon"))
            if lat is not None and lon is not None and self.runtime_context_builder.field_gps_transform_ready():
                converted = self.runtime_context_builder.gps_to_field_xy(lat, lon)
                if converted is not None:
                    copy["field_x"], copy["field_y"] = converted
                    copy["field_coordinate_source"] = "runtime_gps"
                    enriched.append(copy)
                    continue

            enriched.append(copy)
        return enriched

    @staticmethod
    def _float_or_none(value: object) -> float | None:
        return RuntimeContextBuilder._float_or_none(value)

    def active_telemetry_source(self) -> str:
        manager = self.services.state_port
        getter = getattr(manager, "get_active_source", None)
        if callable(getter):
            return str(getter())
        return str(getattr(self.config.telemetry, "active_source", "real"))

def _result_name_is_align_descend(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    reason = str(result.get("reason", ""))
    if reason in {
        "align_descending",
        "confirming_alignment",
        "alignment_confirmed",
        "align_descend_timeout",
        "target_not_found",
        "altitude_unavailable",
    }:
        return True
    detail = result.get("detail")
    return isinstance(detail, dict) and "ex" in detail and "ey" in detail and "altitude_m" in detail
