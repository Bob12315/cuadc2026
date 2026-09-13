from __future__ import annotations

import time
from typing import Any

from execution.authorization import RunAuthorization
from execution.policy import action_requires_run_authorization
from missions.common.actions.result import ActionResult
from missions.engine import MissionActionStep, MissionOrchestrator

# Continuous BODY_NED actions are protected by a 0.5 s deadman.  Refreshing
# at the same interval makes ordinary scheduler jitter indistinguishable from
# a stalled Action, so a live align_descend run can be interrupted by its
# safety STOP.  10 Hz leaves ample margin while retaining the deadman as the
# failure path for a genuinely stalled action.
ACTION_MISSION_TICK_INTERVAL_S = 0.1


class MissionApplicationService:
    """Owns Action Lab and Action Mission lifecycle and run authorization."""

    _RESULT_NAMES = {
        "latest_localization_result": "localization",
        "latest_drop_localization_result": "drop_localization",
        "latest_recon_localization_result": "recon_localization",
        "latest_drop_targets_result": "drop_targets",
        "latest_drop_workflow_result": "drop_workflow",
    }

    def __init__(self, host: Any) -> None:
        object.__setattr__(self, "_host", host)
        self._action_mission_recording_requested = False
        self._action_mission_next_tick_monotonic: float | None = None
        self.action_mission_orchestrator: MissionOrchestrator | None = None

    def __getattr__(self, name: str):
        return getattr(self._host, name)

    def __setattr__(self, name: str, value) -> None:
        result_name = self._RESULT_NAMES.get(name)
        if result_name is not None and "_host" in self.__dict__:
            self._host.result_service.set(result_name, value)
            return
        object.__setattr__(self, name, value)

    def active_telemetry_source(self) -> str:
        return self.system_control.active_source(
            str(getattr(self.config.telemetry, "active_source", "real"))
        )

    def action_lab_tick(self) -> dict[str, object]:
        if not getattr(self, "action_runtime", None):
            return {}
        with self.action_runtime_lock:
            status = self.action_runtime.tick(
                self.action_lab_context(),
                link_manager=None,
                send_commands=bool(self.controller_switches.snapshot().send_commands),
            )
            if not bool(status.get("running", False)):
                self.action_runtime.dispatcher.clear_authorization(
                    f"action_{status.get('state', 'ended')}"
                )
            self._maybe_save_localization_result()
            self._maybe_save_drop_targets_result()
            last = getattr(self.action_runtime, "last_result", None)
            if isinstance(last, dict):
                self._save_drop_workflow_from_action_result(
                    getattr(self.action_runtime, "action_name", None), last)
            self.logger.info(
                "action_lab_tick called current_action=%s dispatch=%s",
                self.action_runtime.action_name,
                self.action_runtime.dispatcher.last_dispatch,
            )
            return status

    def action_lab_status_payload(self) -> dict[str, object]:
        return self.action_runtime.status_payload(
            send_commands=bool(self.controller_switches.snapshot().send_commands),
        )

    def _start_action_mission_recording(self) -> None:
        self._action_mission_recording_requested = True
        self.system_control.recording_start(trigger="action_mission_start")

    def _stop_action_mission_recording(self, *, trigger: str) -> None:
        if not self._action_mission_recording_requested:
            return
        result = self.system_control.recording_stop(trigger=trigger)
        if result.ok:
            self._action_mission_recording_requested = False

    def action_lab_start_action(
        self,
        action_name: str,
        params: dict[str, object] | None = None,
        *,
        authorize: bool = False,
        operator: str = "system",
        target_source: str | None = None,
    ):
        with self.action_runtime_lock:
            requires_authorization = action_requires_run_authorization(action_name)
            if requires_authorization and not authorize:
                self.action_runtime.dispatcher.clear_authorization("run_start_not_authorized")
                return ActionResult(failed=True, reason="run_authorization_required")
            if requires_authorization:
                source = target_source or self.active_telemetry_source()
                authorization = RunAuthorization.create(
                    operator=operator,
                    scope_type="action",
                    scope_name=action_name,
                    target_source=source,
                    allowed_actions={action_name},
                )
                self.action_runtime.dispatcher.set_authorization(authorization)
            else:
                self.action_runtime.dispatcher.clear_authorization("pure_action_start")
            return self.action_runtime.start(
                action_name,
                params,
                link_manager=None,
            )

    def action_lab_stop_action(self):
        with self.action_runtime_lock:
            result = self.action_runtime.stop(
                link_manager=None,
                hold_current=True,
            )
            self.action_runtime.dispatcher.clear_authorization("action_stopped")
            return result

    def action_lab_reset_action(self):
        with self.action_runtime_lock:
            result = self.action_runtime.reset(
                link_manager=None,
                hold_current=True,
            )
            self.action_runtime.dispatcher.clear_authorization("action_reset")
            return result

    # ------------------------------------------------------------------
    # action-mission orchestrator (PR F — lightweight, opt-in)
    # ------------------------------------------------------------------

    def configure_action_mission(self, steps: list[MissionActionStep]) -> None:
        with self.action_runtime_lock:
            self._action_mission_next_tick_monotonic = None
            self.action_mission_orchestrator = MissionOrchestrator(
                runtime=self.action_runtime,
                steps=steps,
            )

    def action_mission_status_payload(self) -> dict[str, object]:
        if self.action_mission_orchestrator is None:
            return {
                "enabled": False,
                "running": False,
                "done": False,
                "failed": False,
                "current_index": 0,
                "current_action": None,
                "reason": "not_configured",
                "detail": {},
            }
        status = self.action_mission_orchestrator.status()
        detail = dict(status.detail)
        detail["blackboard"] = dict(self.action_mission_orchestrator.blackboard.data)
        return {
            "enabled": True,
            "running": status.running,
            "done": status.done,
            "failed": status.failed,
            "current_index": status.current_index,
            "current_action": status.current_action,
            "reason": status.reason,
            "detail": detail,
        }

    def action_mission_start(
        self,
        *,
        authorize: bool = False,
        operator: str = "system",
        target_source: str | None = None,
    ) -> dict[str, object]:
        if self.action_mission_orchestrator is None:
            return self.action_mission_status_payload()
        with self.action_runtime_lock:
            self.latest_drop_localization_result = {}
            self.latest_recon_localization_result = {}
            self.latest_drop_workflow_result = {}
            mission_actions = {step.name for step in self.action_mission_orchestrator.steps}
            requires_authorization = any(
                action_requires_run_authorization(name) for name in mission_actions
            )
            requirements = self._action_mission_field_requirements()
            if requirements["needs_gps"]:
                reason = self._field_gps_mission_preflight_reason()
                if reason is not None:
                    return self._reject_action_mission_start(reason)
            if requires_authorization and not authorize:
                return self._reject_action_mission_start("run_authorization_required")
            if requires_authorization:
                source = target_source or self.active_telemetry_source()
                self.action_runtime.dispatcher.set_authorization(
                    RunAuthorization.create(
                        operator=operator,
                        scope_type="mission",
                        scope_name="action_mission",
                        target_source=source,
                        allowed_actions=mission_actions,
                    )
                )
            else:
                self.action_runtime.dispatcher.clear_authorization("pure_mission_start")
            self.action_mission_orchestrator.start(
                link_manager=None,
            )
            self._action_mission_next_tick_monotonic = time.monotonic()
            payload = self.action_mission_status_payload()
            if payload["running"]:
                self._start_action_mission_recording()
            return payload

    def _action_mission_field_requirements(self) -> dict[str, bool]:
        requirements = {"needs_gps": False}
        orchestrator = self.action_mission_orchestrator
        if orchestrator is None:
            return requirements
        for step in orchestrator.steps:
            if step.name == "goto_waypoint":
                requirements["needs_gps"] = True
        return requirements

    def _field_gps_mission_preflight_reason(self) -> str | None:
        reference = self.field_service.reference
        if not reference.is_confirmed:
            return "field_gps_reference_not_confirmed"
        if not reference.is_frozen:
            return "field_gps_reference_not_frozen"
        if not reference.is_ready_for_field_to_gps():
            return "field_gps_reference_not_ready"

        return None

    def _reject_action_mission_start(
        self,
        reason: str,
        *,
        error: str | None = None,
    ) -> dict[str, object]:
        orchestrator = self.action_mission_orchestrator
        if orchestrator is not None:
            orchestrator.running = False
            orchestrator.done = False
            orchestrator.failed = True
            orchestrator.reason = reason
            orchestrator.detail = {"error": error} if error else {}
        return self.action_mission_status_payload()

    def action_mission_stop(self) -> dict[str, object]:
        if self.action_mission_orchestrator is None:
            return self.action_mission_status_payload()
        with self.action_runtime_lock:
            self._action_mission_next_tick_monotonic = None
            self.action_mission_orchestrator.stop(
                link_manager=None,
                hold_current=True,
            )
            self._stop_action_mission_recording(trigger="action_mission_stop")
            self.action_runtime.dispatcher.clear_authorization("mission_stopped")
            return self.action_mission_status_payload()

    def action_mission_reset(self) -> dict[str, object]:
        if self.action_mission_orchestrator is None:
            return self.action_mission_status_payload()
        with self.action_runtime_lock:
            self._action_mission_next_tick_monotonic = None
            self.latest_drop_localization_result = {}
            self.latest_recon_localization_result = {}
            self.latest_drop_workflow_result = {}
            self.action_mission_orchestrator.reset(
                link_manager=None,
                hold_current=True,
            )
            self._stop_action_mission_recording(trigger="action_mission_reset")
            self.action_runtime.dispatcher.clear_authorization("mission_reset")
            return self.action_mission_status_payload()

    def action_mission_tick(self) -> dict[str, object]:
        if self.action_mission_orchestrator is None:
            return self.action_mission_status_payload()
        with self.action_runtime_lock:
            self._action_mission_next_tick_monotonic = (
                time.monotonic() + ACTION_MISSION_TICK_INTERVAL_S
            )
            orch = self.action_mission_orchestrator
            pre_index = orch.current_index
            pre_step = orch.steps[pre_index] if 0 <= pre_index < len(orch.steps) else None
            pre_name = pre_step.name if pre_step else None
            pre_label = pre_step.label if pre_step else None

            mission_status = orch.tick(
                self.action_lab_context(),
                link_manager=None,
                send_commands=bool(self.controller_switches.snapshot().send_commands),
            )
            self._maybe_save_localization_from_mission()
            self._maybe_save_localization_result()
            self._maybe_save_drop_targets_result()
            # current running action
            last = getattr(self.action_runtime, "last_result", None)
            if isinstance(last, dict):
                cur_index = orch.current_index
                cur_step = orch.steps[cur_index] if 0 <= cur_index < len(orch.steps) else None
                self._save_drop_workflow_from_action_result(
                    cur_step.name if cur_step else getattr(self.action_runtime, "action_name", None),
                    last,
                    step_index=cur_index,
                    step_label=cur_step.label if cur_step else None)
            # previous step result from orchestrator
            ms_detail = mission_status.detail if isinstance(mission_status.detail, dict) else {}
            prev_result = ms_detail.get("previous_action_result")
            if isinstance(prev_result, dict):
                self._save_drop_workflow_from_action_result(
                    pre_name, prev_result,
                    step_index=pre_index, step_label=pre_label)
            # final result when mission done
            final_result = ms_detail.get("action_result")
            if isinstance(final_result, dict):
                self._save_drop_workflow_from_action_result(
                    pre_name, final_result,
                    step_index=pre_index, step_label=pre_label)
            if not mission_status.running:
                self.action_runtime.dispatcher.clear_authorization(
                    f"mission_{mission_status.reason}"
                )
                self._stop_action_mission_recording(
                    trigger=f"action_mission_{mission_status.reason}"
                )
            return mission_status

    def _tick_action_mission_in_background(
        self,
        *,
        now_monotonic: float | None = None,
    ) -> bool:
        """Advance the one active Action Mission or Action Lab run off the Web thread."""
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        with self.action_runtime_lock:
            orchestrator = self.action_mission_orchestrator
            mission_running = orchestrator is not None and orchestrator.running
            action_lab_running = bool(
                getattr(getattr(self, "action_runtime", None), "runner", None)
                and self.action_runtime.runner.state == "running"
            )
            if not mission_running and not action_lab_running:
                self._action_mission_next_tick_monotonic = None
                return False
            deadline = self._action_mission_next_tick_monotonic
            if deadline is not None and now < deadline:
                return False
            if not mission_running:
                self._action_mission_next_tick_monotonic = now + ACTION_MISSION_TICK_INTERVAL_S
        try:
            if mission_running:
                self.action_mission_tick()
            else:
                self.action_lab_tick()
        except Exception:
            if mission_running:
                self.logger.exception("autonomous Action Mission tick failed")
                self.action_mission_stop()
            else:
                self.logger.exception("autonomous Action Lab tick failed")
                self.action_lab_stop_action()
            return False
        return True

    def action_mission_skip_current(self) -> dict[str, object]:
        if self.action_mission_orchestrator is None:
            return self.action_mission_status_payload()
        with self.action_runtime_lock:
            mission_status = self.action_mission_orchestrator.skip_current_step(
                link_manager=None,
                hold_current=True,
                reason="manual_web_skip",
            )
            if not mission_status.running:
                self.action_runtime.dispatcher.clear_authorization(
                    f"mission_{mission_status.reason}"
                )
                self._stop_action_mission_recording(
                    trigger=f"action_mission_{mission_status.reason}"
                )
            self._record_event("WARN", "action mission current step skipped manually")
            return self.action_mission_status_payload()
