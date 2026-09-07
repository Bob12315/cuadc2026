"""Explicit application surface exposed to the Web adapter."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class SystemControlPort(Protocol):
    def set_send(self, enabled: bool): ...
    def switch_source(self, source: str): ...
    def reconnect(self): ...
    def restart_service(self, service: str): ...
    def target_command(self, command: str): ...
    def reset_virtual_nadir(self): ...
    def recording_status(self) -> dict[str, Any]: ...
    def recording_toggle(self): ...


class MissionControlPort(Protocol):
    def active_telemetry_source(self) -> str: ...
    def action_lab_status_payload(self) -> dict[str, Any]: ...
    def action_lab_start_action(self, name: str, params: dict[str, Any], **kwargs): ...
    def action_lab_tick(self): ...
    def action_lab_stop_action(self): ...
    def action_lab_reset_action(self): ...
    def action_mission_status_payload(self) -> dict[str, Any]: ...
    def configure_action_mission(self, steps: list[Any]) -> None: ...
    def action_mission_start(self, **kwargs): ...
    def action_mission_stop(self): ...
    def action_mission_reset(self): ...
    def action_mission_tick(self): ...
    def action_mission_skip_current(self): ...


@dataclass(frozen=True, slots=True)
class WebServices:
    """Narrow facade; Web code never receives ``SystemRunner``."""

    system_control: SystemControlPort
    mission_control: MissionControlPort
    status_snapshot: Callable[[], dict[str, Any]]
    field_reference_status: Callable[[], dict[str, Any]]
    field_reference_reset: Callable[[], dict[str, Any]]
    field_reference_freeze: Callable[[], dict[str, Any]]
    field_profile_list: Callable[[], dict[str, Any]]
    field_profile_get: Callable[[str], dict[str, Any]]
    field_profile_validate: Callable[[str], dict[str, Any]]
    runtime_sampling_start: Callable[[str], dict[str, Any]]
    runtime_sampling_finalize: Callable[[], dict[str, Any]]
    runtime_sampling_cancel: Callable[[], dict[str, Any]]
    competition_sampling_start: Callable[[float, float], dict[str, Any]]
    clear_localization: Callable[[], Any]
    action_specs: tuple[dict[str, Any], ...]
    action_lab_enabled: bool
    authorization_snapshot: Callable[[], Any]

    @classmethod
    def from_runner(cls, runner: Any) -> "WebServices":
        """Composition-root adapter. Kept outside ``web_ui`` by design."""
        runtime = runner.action_runtime

        def authorization_snapshot():
            return runtime.dispatcher.authorization

        return cls(
            system_control=runner.system_control,
            mission_control=runner.mission_service,
            status_snapshot=runner.web_status_snapshot,
            field_reference_status=runner.field_reference_status,
            field_reference_reset=runner.field_reference_reset,
            field_reference_freeze=runner.field_reference_freeze,
            field_profile_list=runner.field_profile_list,
            field_profile_get=runner.field_profile_get,
            field_profile_validate=runner.field_profile_validate,
            runtime_sampling_start=runner.field_profile_runtime_sampling_start,
            runtime_sampling_finalize=runner.field_profile_runtime_sampling_finalize,
            runtime_sampling_cancel=runner.field_profile_runtime_sampling_cancel,
            competition_sampling_start=runner.competition_runtime_sampling_start,
            clear_localization=runner.clear_localization_result,
            action_specs=tuple(runner.action_lab_specs),
            action_lab_enabled=bool(runner.action_lab_enabled),
            authorization_snapshot=authorization_snapshot,
        )
