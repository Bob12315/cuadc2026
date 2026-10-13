from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable


_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MissionActionStep:
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    save_as: str | None = None
    label: str | None = None
    on_failed: dict[str, Any] | None = None


@dataclass(slots=True)
class MissionOrchestratorStatus:
    running: bool
    paused: bool
    done: bool
    failed: bool
    current_index: int
    current_action: str | None
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


class MissionBlackboard:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def clear(self) -> None:
        self.data.clear()

    def set(self, name: str, value: Any) -> None:
        normalized = self._normalize_name(name)
        self.data[normalized] = value

    def get_path(self, path: str) -> Any:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("blackboard path must be a non-empty string")
        current: Any = self.data
        for part in path.strip().split("."):
            if not part:
                raise ValueError("blackboard path contains an empty segment")
            if isinstance(current, dict):
                if part not in current:
                    raise KeyError(part)
                current = current[part]
                continue
            if isinstance(current, list):
                try:
                    index = int(part)
                except ValueError as exc:
                    raise ValueError(f"list index must be an integer: {part}") from exc
                try:
                    current = current[index]
                except IndexError as exc:
                    raise KeyError(part) from exc
                continue
            raise KeyError(part)
        return current

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            if value.startswith("$"):
                return self.get_path(value[1:])
            return value
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    def _normalize_name(self, name: str) -> str:
        if not isinstance(name, str):
            raise ValueError("blackboard name must be a non-empty string")
        normalized = name.strip()
        if not normalized:
            raise ValueError("blackboard name must be a non-empty string")
        return normalized


class MissionOrchestrator:
    """Minimal mission sequencer — drives ActionRuntimeService step by step.

    This orchestrator does NOT talk to LinkManager, pymavlink, or
    concrete Action classes directly.  All vehicle interaction flows
    through the ActionRuntimeService the caller injects.
    """

    def __init__(
        self,
        runtime: object,
        steps: list[MissionActionStep],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not steps:
            raise ValueError("steps must be non-empty")
        self.runtime = runtime
        self.steps = list(steps)
        self.running = False
        self.paused = False
        self._paused_at_monotonic: float | None = None
        self.done = False
        self.failed = False
        self.current_index = 0
        self.reason = "idle"
        self.detail: dict[str, Any] = {}
        self.blackboard = MissionBlackboard()
        self.step_attempts: dict[int, int] = {}
        self.failure_policy_counts: dict[str, int] = {}
        self.skipped_steps: list[dict[str, Any]] = []
        self._monotonic = monotonic
        self._mission_started_monotonic: float | None = None
        self._mission_finished_monotonic: float | None = None
        self._step_timings: dict[int, dict[str, Any]] = {}
        self.labels = self._build_labels()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self, *, link_manager: object | None = None) -> None:
        self.running = True
        self.paused = False
        self._paused_at_monotonic = None
        self.done = False
        self.failed = False
        self.current_index = 0
        self.reason = "started"
        self.detail = {}
        self.blackboard.clear()
        self.step_attempts.clear()
        self.failure_policy_counts.clear()
        self.skipped_steps.clear()
        self._mission_started_monotonic = self._monotonic()
        self._mission_finished_monotonic = None
        self._step_timings.clear()
        self._start_current_step(link_manager=link_manager)

    def tick(
        self,
        context: dict[str, Any],
        *,
        link_manager: object | None = None,
        send_commands: bool = False,
    ) -> MissionOrchestratorStatus:
        if not self.running or self.paused or self.done or self.failed:
            return self.status()

        # Drive the current action
        _status = self.runtime.tick(
            context,
            link_manager=link_manager,
            send_commands=send_commands,
        )

        # Read the ActionResult that ActionRuntimeService just processed
        result: dict[str, Any] = {}
        last_result_obj = getattr(self.runtime, "last_result", None)
        if last_result_obj is not None:
            if isinstance(last_result_obj, dict):
                result = last_result_obj
            else:
                result = last_result_obj.to_dict()

        if bool(result.get("failed")):
            self._handle_step_failed(result, link_manager=link_manager)
            return self.status()

        if bool(result.get("done")):
            step = self.steps[self.current_index]
            if step.save_as:
                try:
                    output = result.get("output") if isinstance(result.get("output"), dict) else {}
                    self.blackboard.set(step.save_as, output)
                except Exception as exc:
                    self.failed = True
                    self.running = False
                    self.reason = "blackboard_save_failed"
                    self.detail = {
                        "step_index": self.current_index,
                        "step_name": step.name,
                        "save_as": step.save_as,
                        "error": str(exc),
                        "blackboard_keys": sorted(self.blackboard.data.keys()),
                    }
                    self._finish_step_timing(
                        self.current_index,
                        status="failed",
                        reason=self.reason,
                    )
                    self._finish_mission_timing()
                    return self.status()

            self._finish_step_timing(
                self.current_index,
                status="done",
                reason=str(result.get("reason") or "done"),
            )
            if self.current_index + 1 >= len(self.steps):
                self.done = True
                self.running = False
                self.reason = "mission_done"
                self.detail = {"action_result": result}
                self._finish_mission_timing()
                return self.status()

            previous_step = self.steps[self.current_index]
            next_step = self.steps[self.current_index + 1]
            hold_before_payload_release = (
                previous_step.name == "align_descend"
                and next_step.name == "payload_release"
            )
            self.current_index += 1
            self.reason = "next_step"
            self.detail = {"previous_action_result": result}
            # Clear any stale LOCAL_POSITION before starting the next step
            clear_nav = getattr(self.runtime, "clear_navigation_queue", None)
            if callable(clear_nav):
                if hold_before_payload_release:
                    _log.info(
                        "hold current local position before payload_release after align_descend"
                    )
                    clear_nav(
                        link_manager,
                        hold_current=True,
                        leave_stop_queued=True,
                    )
                else:
                    clear_nav(link_manager)
            self._start_current_step(link_manager=link_manager, clear_navigation=False)

        return self.status()

    def pause(self, *, reason: str = "flight_mode_not_guided", mode: str = "UNKNOWN") -> None:
        if not self.running or self.paused:
            return
        self.paused = True
        self._paused_at_monotonic = self._monotonic()
        self.reason = reason
        self.detail = {"flight_mode": mode, "paused_step_index": self.current_index}

    def resume(self) -> float:
        if not self.running or not self.paused or self._paused_at_monotonic is None:
            return 0.0
        duration_s = max(0.0, self._monotonic() - self._paused_at_monotonic)
        if self._mission_started_monotonic is not None:
            self._mission_started_monotonic += duration_s
        timing = self._step_timings.get(self.current_index)
        if timing is not None and timing.get("running_since_monotonic") is not None:
            timing["running_since_monotonic"] += duration_s
        self.paused = False
        self._paused_at_monotonic = None
        self.reason = "resumed_guided"
        self.detail = {"pause_duration_s": round(duration_s, 3)}
        return duration_s

    def stop(self, *, link_manager: object | None = None, hold_current: bool = False) -> None:
        stop = getattr(self.runtime, "stop", None)
        if callable(stop):
            stop(link_manager, hold_current=hold_current)
        self._finish_step_timing(
            self.current_index,
            status="stopped",
            reason="stopped",
        )
        self._finish_mission_timing()
        self.running = False
        self.paused = False
        self._paused_at_monotonic = None
        self.reason = "stopped"

    def reset(self, *, link_manager: object | None = None, hold_current: bool = False) -> None:
        reset = getattr(self.runtime, "reset", None)
        if callable(reset):
            reset(link_manager, hold_current=hold_current)
        self.running = False
        self.paused = False
        self._paused_at_monotonic = None
        self.done = False
        self.failed = False
        self.current_index = 0
        self.reason = "reset"
        self.detail = {}
        self.blackboard.clear()
        self.step_attempts.clear()
        self.failure_policy_counts.clear()
        self.skipped_steps.clear()
        self._mission_started_monotonic = None
        self._mission_finished_monotonic = None
        self._step_timings.clear()

    def skip_current_step(
        self,
        *,
        link_manager: object | None = None,
        hold_current: bool = True,
        reason: str = "manual_skip",
    ) -> MissionOrchestratorStatus:
        """Skip the currently-running step, stop its action, clear
        navigation, and advance to the next step (or mark done).

        The blackboard is NOT cleared; skipped steps are recorded so
        the UI timeline can show a distinct ``skipped`` status.
        """

        if self.done:
            self.reason = "skip_ignored_done"
            self.detail = {"requested_reason": reason}
            return self.status()

        if self.paused:
            self.reason = "skip_ignored_paused"
            return self.status()

        if self.failed:
            self.reason = "skip_ignored_failed"
            self.detail = {"requested_reason": reason}
            return self.status()

        if not self.running:
            self.reason = "skip_ignored_not_running"
            self.detail = {
                "requested_reason": reason,
                "current_index": self.current_index,
            }
            return self.status()

        step = self.steps[self.current_index]
        skipped_index = self.current_index
        skipped_name = step.name
        skipped_label = step.label

        self._finish_step_timing(
            skipped_index,
            status="skipped",
            reason=reason,
        )

        # 1. stop the runtime action
        stop = getattr(self.runtime, "stop", None)
        if callable(stop):
            stop(link_manager, hold_current=hold_current)

        # 2. belt-and-braces: clear any stale LOCAL_POSITION / velocity
        clear_nav = getattr(self.runtime, "clear_navigation_queue", None)
        if callable(clear_nav):
            clear_nav(link_manager, hold_current=hold_current)

        # 3. record the skipped step for the UI timeline
        self.skipped_steps.append(
            {
                "index": skipped_index,
                "name": skipped_name,
                "label": skipped_label,
                "reason": reason,
                "time": time.time(),
            }
        )

        # 4. advance — or finish if this was the last step
        if self.current_index + 1 >= len(self.steps):
            self.done = True
            self.running = False
            self.failed = False
            self.reason = "mission_done_after_manual_skip"
            self.detail = {
                "skipped_step_index": skipped_index,
                "skipped_step_name": skipped_name,
                "skipped_step_label": skipped_label,
                "requested_reason": reason,
                "blackboard_keys": sorted(self.blackboard.data.keys()),
            }
            self._finish_mission_timing()
            return self.status()

        self.current_index += 1
        self.reason = "manual_skip_to_next_step"
        self.detail = {
            "skipped_step_index": skipped_index,
            "skipped_step_name": skipped_name,
            "skipped_step_label": skipped_label,
            "next_step_index": self.current_index,
            "next_step_name": self.steps[self.current_index].name,
            "next_step_label": self.steps[self.current_index].label,
            "requested_reason": reason,
            "blackboard_keys": sorted(self.blackboard.data.keys()),
        }
        self._start_current_step(link_manager=link_manager)
        return self.status()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def status(self) -> MissionOrchestratorStatus:
        current = None
        if 0 <= self.current_index < len(self.steps):
            current = self.steps[self.current_index].name
        detail = dict(self.detail)
        detail["blackboard_keys"] = sorted(self.blackboard.data.keys())
        detail["step_attempts"] = dict(self.step_attempts)
        detail["failure_policy_counts"] = dict(self.failure_policy_counts)
        detail["skipped_steps"] = list(self.skipped_steps)
        detail["step_timings"] = self._step_timing_payload()
        detail["mission_duration_s"] = self._mission_duration_s()
        return MissionOrchestratorStatus(
            running=self.running,
            paused=self.paused,
            done=self.done,
            failed=self.failed,
            current_index=self.current_index,
            current_action=current,
            reason=self.reason,
            detail=detail,
        )

    def _start_current_step(
        self,
        *,
        link_manager: object | None = None,
        clear_navigation: bool = True,
    ) -> None:
        step = self.steps[self.current_index]
        self._start_step_timing(self.current_index)
        start = getattr(self.runtime, "start", None)
        if callable(start):
            try:
                resolved_params = self.blackboard.resolve(dict(step.params))
            except Exception as exc:
                result = {
                    "failed": True,
                    "done": False,
                    "reason": "param_resolution_failed",
                    "detail": {
                        "step_index": self.current_index,
                        "step_name": step.name,
                        "error": str(exc),
                        "blackboard_keys": sorted(self.blackboard.data.keys()),
                    },
                }
                self._handle_step_failed(result, link_manager=link_manager)
                return
            self.step_attempts[self.current_index] = self.step_attempts.get(self.current_index, 0) + 1
            start(
                step.name,
                resolved_params,
                link_manager=link_manager,
                clear_navigation=clear_navigation,
            )

    def _handle_step_failed(
        self,
        result: dict[str, Any],
        *,
        link_manager: object | None = None,
    ) -> None:
        step = self.steps[self.current_index]
        policy = step.on_failed or {}
        action = str(policy.get("action", "fail")).strip().lower()
        if action == "retry_current":
            self._handle_retry_current(result, policy, link_manager=link_manager)
            return
        if action == "retry_current_then_jump_to":
            self._handle_retry_current_then_jump_to(result, policy, link_manager=link_manager)
            return
        if action == "jump_to":
            self._handle_jump_to(result, policy, link_manager=link_manager)
            return
        if action == "continue":
            self._handle_continue(result, link_manager=link_manager)
            return
        self._fail_mission(result, reason=str(result.get("reason") or "action_failed"))

    def _handle_retry_current(
        self,
        result: dict[str, Any],
        policy: dict[str, Any],
        *,
        link_manager: object | None,
    ) -> None:
        max_attempts = int(policy.get("max_attempts", 1))
        attempts = self.step_attempts.get(self.current_index, 1)
        if attempts < max_attempts:
            self.reason = "retry_current"
            self.detail = {
                "failed_action_result": result,
                "retry_step_index": self.current_index,
                "attempt": attempts + 1,
                "max_attempts": max_attempts,
            }
            self._clear_runtime_before_retry(link_manager)
            self._start_current_step(link_manager=link_manager)
            return
        self._fail_mission(result, reason="retry_attempts_exhausted")

    def _handle_retry_current_then_jump_to(
        self,
        result: dict[str, Any],
        policy: dict[str, Any],
        *,
        link_manager: object | None,
    ) -> None:
        max_attempts = int(policy.get("max_attempts", 1))
        attempts = self.step_attempts.get(self.current_index, 1)
        if attempts < max_attempts:
            self.reason = "retry_current"
            self.detail = {
                "failed_action_result": result,
                "retry_step_index": self.current_index,
                "attempt": attempts + 1,
                "max_attempts": max_attempts,
            }
            self._clear_runtime_before_retry(link_manager)
            self._start_current_step(link_manager=link_manager)
            return

        target = str(policy.get("target") or "").strip()
        if target not in self.labels:
            self._fail_mission(result, reason="retry_jump_target_not_found")
            return

        failed_index = self.current_index
        self._finish_step_timing(
            failed_index,
            status="continued",
            reason=str(result.get("reason") or "retry_attempts_exhausted"),
        )
        self.current_index = self.labels[target]
        self.reason = "retry_current_then_jump_to"
        self.detail = {
            "failed_action_result": result,
            "target": target,
            "target_index": self.current_index,
            "max_attempts": max_attempts,
        }
        self._clear_runtime_before_retry(link_manager)
        self._start_current_step(link_manager=link_manager)

    def _handle_jump_to(
        self,
        result: dict[str, Any],
        policy: dict[str, Any],
        *,
        link_manager: object | None,
    ) -> None:
        target = str(policy.get("target") or "").strip()
        if target not in self.labels:
            self._fail_mission(result, reason="jump_target_not_found")
            return
        max_attempts = int(policy.get("max_attempts", 1))
        key = f"{self.current_index}:jump_to:{target}"
        count = self.failure_policy_counts.get(key, 0)
        if count < max_attempts:
            self.failure_policy_counts[key] = count + 1
            failed_index = self.current_index
            self._finish_step_timing(
                failed_index,
                status="continued",
                reason=str(result.get("reason") or "action_failed"),
            )
            self.current_index = self.labels[target]
            self.reason = "jump_to"
            self.detail = {
                "failed_action_result": result,
                "target": target,
                "target_index": self.current_index,
                "policy_count": count + 1,
                "max_attempts": max_attempts,
            }
            self._clear_runtime_before_retry(link_manager)
            self._start_current_step(link_manager=link_manager)
            return
        self._fail_mission(result, reason="jump_attempts_exhausted")

    def _handle_continue(self, result: dict[str, Any], *, link_manager: object | None) -> None:
        step = self.steps[self.current_index]
        failure_reason = str(result.get("reason") or "action_failed")
        self._finish_step_timing(
            self.current_index,
            status="continued",
            reason=failure_reason,
        )
        self.skipped_steps.append(
            {
                "index": self.current_index,
                "name": step.name,
                "label": step.label,
                "reason": failure_reason,
                "time": time.time(),
            }
        )
        if self.current_index + 1 >= len(self.steps):
            self.done = True
            self.running = False
            self.reason = "mission_done_after_failed_continue"
            self.detail = {"failed_action_result": result}
            self._finish_mission_timing()
            return
        self.current_index += 1
        self.reason = "continue_after_failed_step"
        self.detail = {"failed_action_result": result}
        self._clear_runtime_before_retry(link_manager)
        self._start_current_step(link_manager=link_manager)

    def _fail_mission(self, result: dict[str, Any], *, reason: str) -> None:
        self._finish_step_timing(
            self.current_index,
            status="failed",
            reason=reason,
        )
        self._finish_mission_timing()
        self.failed = True
        self.running = False
        self.reason = reason
        self.detail = {"action_result": result}

    def _clear_runtime_before_retry(self, link_manager: object | None = None) -> None:
        clear_nav = getattr(self.runtime, "clear_navigation_queue", None)
        if callable(clear_nav):
            clear_nav(link_manager, hold_current=True)
        reset = getattr(self.runtime, "reset", None)
        if callable(reset):
            reset(link_manager, hold_current=True)

    def _start_step_timing(self, index: int) -> None:
        now = self._monotonic()
        timing = self._step_timings.setdefault(
            index,
            {
                "duration_s": 0.0,
                "running_since_monotonic": None,
                "status": "pending",
                "reason": "",
            },
        )
        if timing.get("running_since_monotonic") is not None:
            return
        timing["running_since_monotonic"] = now
        timing["status"] = "running"
        timing["reason"] = ""

    def _finish_step_timing(self, index: int, *, status: str, reason: str) -> None:
        timing = self._step_timings.get(index)
        if timing is None:
            return
        started = timing.get("running_since_monotonic")
        if started is not None:
            now = self._paused_at_monotonic if self._paused_at_monotonic is not None else self._monotonic()
            timing["duration_s"] = float(timing.get("duration_s", 0.0)) + max(
                0.0,
                now - float(started),
            )
        timing["running_since_monotonic"] = None
        timing["status"] = status
        timing["reason"] = reason

    def _finish_mission_timing(self) -> None:
        if self._mission_started_monotonic is None:
            return
        if self._mission_finished_monotonic is None:
            self._mission_finished_monotonic = (
                self._paused_at_monotonic if self._paused_at_monotonic is not None else self._monotonic()
            )

    def _mission_duration_s(self) -> float:
        started = self._mission_started_monotonic
        if started is None:
            return 0.0
        finished = self._mission_finished_monotonic
        end = (
            self._paused_at_monotonic if self._paused_at_monotonic is not None else self._monotonic()
        ) if finished is None else finished
        return round(max(0.0, end - started), 3)

    def _step_timing_payload(self) -> dict[int, dict[str, Any]]:
        now = self._paused_at_monotonic if self._paused_at_monotonic is not None else self._monotonic()
        payload: dict[int, dict[str, Any]] = {}
        for index in range(len(self.steps)):
            timing = self._step_timings.get(index)
            if timing is None:
                payload[index] = {
                    "duration_s": 0.0,
                    "status": "pending",
                    "reason": "",
                }
                continue
            duration = float(timing.get("duration_s", 0.0))
            started = timing.get("running_since_monotonic")
            if started is not None:
                duration += max(0.0, now - float(started))
            payload[index] = {
                "duration_s": round(duration, 3),
                "status": str(timing.get("status") or "pending"),
                "reason": str(timing.get("reason") or ""),
            }
        return payload

    def _build_labels(self) -> dict[str, int]:
        labels: dict[str, int] = {}
        for index, step in enumerate(self.steps):
            if step.label is None:
                continue
            label = step.label.strip()
            if not label:
                continue
            if label in labels:
                raise ValueError(f"duplicate mission step label: {label}")
            labels[label] = index
        return labels
