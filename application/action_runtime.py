from __future__ import annotations

import logging
from missions.common.actions.runner import ActionRunner
from execution.dispatcher import ActionDispatcher


_log = logging.getLogger(__name__)


class ActionRuntimeService:
    """Thin orchestrator that owns ActionRunner + ActionDispatcher.

    SystemRunner delegates its public action_lab_* methods here so that
    the runner lifecycle and dispatch plumbing stay in one place.
    """

    def __init__(self, *, runner: ActionRunner, dispatcher: ActionDispatcher | None = None) -> None:
        self.runner = runner
        self.dispatcher = dispatcher or ActionDispatcher()
        self.last_result: dict[str, object] | None = None

    # ------------------------------------------------------------------
    # convenience properties
    # ------------------------------------------------------------------

    @property
    def action_name(self) -> str | None:
        return self.runner.action_name

    # ------------------------------------------------------------------
    # public API — mirrors SystemRunner.action_lab_*
    # ------------------------------------------------------------------

    def start(
        self,
        action_name: str,
        params: dict[str, object] | None = None,
        *,
        link_manager: object | None = None,
        clear_navigation: bool = True,
    ):
        link_manager = link_manager or self.dispatcher.command_port
        preempted: dict[str, object] | None = None
        if self.runner.state == "running" and self.runner.action_name:
            active_name = self.runner.action_name
            # A same-type goto is a navigation target replacement, not an
            # attempt to start a second Action.  Other same-type Actions keep
            # the historic action_already_running behaviour.
            if active_name == action_name == "goto_waypoint":
                old_action_id = self.runner.action_id
                old_target = dict(self.runner.active_target or {})
                cancelled = self.runner.stop(reason="superseded_by_new_goto")
                preempted = {
                    "old_action_id": old_action_id,
                    "old_target": old_target,
                    "new_target": self._target_summary(params),
                    "preempt_reason": "superseded_by_new_goto",
                    "old_lifecycle_state": cancelled.detail.get("lifecycle_state"),
                }
                _log.info(
                    "goto preempted old_action_id=%s old_target=%s new_target=%s reason=%s",
                    old_action_id,
                    old_target,
                    preempted["new_target"],
                    "superseded_by_new_goto",
                )
            elif active_name != action_name:
                # Preserve the existing Action Lab switch behaviour for
                # distinct action classes.
                self.runner.stop()
            else:
                result = self.runner.start(action_name, dict(params or {}))
                self.last_result = result.to_dict()
                return result
        if clear_navigation:
            self.clear_navigation_queue(link_manager)
        self.dispatcher.reset_keys()
        self.dispatcher.last_dispatch = self.dispatcher.empty_dispatch()
        self.dispatcher.last_servo_command = None
        result = self.runner.start(action_name, dict(params or {}))
        if preempted is not None:
            detail = dict(result.detail)
            detail["preempted_action"] = preempted
            result = type(result)(
                effects=result.effects,
                done=result.done,
                failed=result.failed,
                reason=result.reason,
                output=result.output,
                detail=detail,
            )
        # MissionOrchestrator consumes ``last_result`` on its next tick.  The
        # start result must replace any result left by the preceding Action;
        # otherwise a failed start can leave a stale success visible and
        # incorrectly advance the mission.
        self.last_result = result.to_dict()
        return result

    def tick(
        self,
        context: dict[str, object],
        *,
        link_manager: object | None,
        send_commands: bool,
    ) -> dict[str, object]:
        link_manager = link_manager or self.dispatcher.command_port
        if self.runner.state != "running":
            return self.runner.status()
        active_name = self.runner.action_name
        result = self.runner.update(context)
        result_dict = result.to_dict()
        self.last_result = result_dict
        if active_name == "goto_waypoint" and result.failed:
            self.clear_navigation_queue(link_manager, hold_current=True)
        self.dispatcher.last_dispatch = self.dispatcher.dispatch_result(
            result,
            action_name=active_name,
            link_manager=link_manager,
            send_commands=send_commands,
        )
        if active_name == "goto_waypoint" and self.runner.state == "running":
            self._record_goto_dispatch_rejections()
        if self.runner.state in {"succeeded", "failed", "cancelled"}:
            self.dispatcher.release_action_keys()
            _log.info(
                "action terminal cleanup action=%s state=%s reason=%s active_action_cleared=%s",
                active_name,
                self.runner.state,
                result.reason,
                self.runner.current_action is None,
            )
        return self.runner.status()

    def stop(self, link_manager: object | None = None, *, hold_current: bool = False):
        """Stop the running action and optionally hold current position."""
        link_manager = link_manager or self.dispatcher.command_port
        self.clear_navigation_queue(link_manager, hold_current=hold_current)
        self.dispatcher.last_dispatch = self.dispatcher.empty_dispatch()
        result = self.runner.stop()
        self.last_result = result.to_dict()
        if self.runner.state in {"succeeded", "failed", "cancelled"}:
            self.dispatcher.release_action_keys()
            _log.info(
                "action terminal cleanup action=cancelled state=%s reason=%s active_action_cleared=%s",
                self.runner.state,
                result.reason,
                self.runner.current_action is None,
            )
        return result

    def reset(self, link_manager: object | None = None, *, hold_current: bool = False):
        """Reset the runtime and optionally hold current position."""
        link_manager = link_manager or self.dispatcher.command_port
        self.clear_navigation_queue(link_manager, hold_current=hold_current)
        if self.runner.current_action is not None and self.runner.state == "running":
            self.runner.stop()
        self.dispatcher.reset_keys()
        self.dispatcher.last_dispatch = self.dispatcher.empty_dispatch()
        self.dispatcher.last_servo_command = None
        self.last_result = None
        return self.runner.reset()

    def status(self) -> dict[str, object]:
        return self.runner.status()

    def clear_navigation_queue(
        self,
        link_manager: object | None = None,
        *,
        hold_current: bool = False,
        leave_stop_queued: bool = False,
    ) -> None:
        """Clear continuous commands and pending LOCAL_POSITION; optionally send a hold.

        Default path uses atomic stop-and-clear to guarantee the STOP is
        transmitted before the queue is cleared.  This avoids the race where
        stop_body_velocity() writes a STOP and the immediate
        clear_continuous_commands() removes it before CommandSender sends it.

        leave_stop_queued=True preserves the old semantics for the
        align_descend → payload_release transition where a persistent STOP
        must remain queued.
        """
        if isinstance(self, ActionRuntimeService):
            link_manager = link_manager or self.dispatcher.command_port
        else:
            # Callable as an unbound utility for adapter contract tests.
            link_manager = self
        if link_manager is None:
            return

        stop_and_clear = getattr(link_manager, "stop_body_velocity_and_clear", None)
        stop_body = getattr(link_manager, "stop_body_velocity", None)
        clear_continuous = getattr(link_manager, "clear_continuous_commands", None)
        clear_nav = getattr(link_manager, "clear_pending_local_position_actions", None)

        if leave_stop_queued:
            # Preserve old semantics: clear first, then enqueue a persistent
            # STOP that stays in the queue for the next action to refresh.
            if callable(clear_continuous):
                clear_continuous()
            if callable(clear_nav):
                clear_nav()
            if callable(stop_body):
                _log.info("queue persistent stop BODY_NED velocity for payload transition")
                stop_body()
        else:
            # Atomic stop-and-clear: STOP is guaranteed to be sent before clear.
            if callable(stop_and_clear):
                _log.info("queue stop-and-clear BODY_NED velocity before clearing navigation")
                stop_and_clear()
            elif callable(stop_body):
                _log.warning(
                    "stop_body_velocity_and_clear unavailable; "
                    "queue STOP without immediate clear (stop will be sent by CommandSender)"
                )
                stop_body()
            else:
                if callable(clear_continuous):
                    clear_continuous()
            if callable(clear_nav):
                clear_nav()

        if hold_current:
            hold = getattr(link_manager, "hold_current_local_position", None)
            if callable(hold):
                hold()

    def status_payload(self, *, send_commands: bool) -> dict[str, object]:
        return self.dispatcher.payload(
            status=self.runner.status(),
            action_name=self.runner.action_name,
            send_commands=send_commands,
        )

    def _record_goto_dispatch_rejections(self) -> None:
        """Relay a safety-pipeline control rejection into goto lifecycle.

        State telemetry is normally sufficient, but the dispatcher can know
        about a rejected GLOBAL_GOTO one tick earlier.  This closes that gap
        without coupling the Action directly to LinkManager or MAVLink.
        """
        action = self.runner.current_action
        observer = getattr(action, "observe_dispatch_rejection", None)
        if not callable(observer):
            return
        for skipped in self.dispatcher.last_dispatch.get("skipped", []):
            reason = str(skipped.get("reason") or "")
            if reason in {
                "control_not_allowed",
                "flight_mode_not_guided",
                "guided_mode_lost",
            }:
                observer(reason)

    @staticmethod
    def _target_summary(params: dict[str, object] | None) -> dict[str, object]:
        data = dict(params or {})
        target = data.get("target")
        source = target if isinstance(target, dict) else data
        result: dict[str, object] = {}
        for key, legacy in (("field_x_m", "x"), ("field_y_m", "y")):
            value = source.get(key, source.get(legacy))
            if value is not None:
                result[key] = value
        for key in ("lat", "lon"):
            if source.get(key) is not None:
                result[key] = source[key]
        if data.get("altitude_m") is not None:
            result["alt_m"] = data["altitude_m"]
        return result
