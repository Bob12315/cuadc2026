from __future__ import annotations

import logging
import uuid
from typing import Any

from .base import ActionModule
from .registry import ActionRegistry, default_registry
from .result import ActionResult


class ActionRunner:
    """Own exactly one active Action and clear it on every terminal path."""

    def __init__(self, registry: ActionRegistry | None = None):
        self.registry = registry or default_registry
        self.state = "idle"
        self.action_name: str | None = None
        self.action_id: str | None = None
        self.current_action: ActionModule | None = None
        self.active_target: dict[str, Any] | None = None
        self.last_terminal: dict[str, Any] | None = None
        self.last_result = ActionResult()

    def start(
        self,
        action_name: str,
        params: dict[str, Any] | None = None,
    ) -> ActionResult:
        if self.state == "running" and self.current_action is not None:
            return self._set_result(
                ActionResult(
                    failed=True,
                    reason="action_already_running",
                    detail={
                        "requested_action": action_name,
                        "requested_target": self._target_from_params(params),
                        "active_action": self.action_name,
                        "active_action_id": self.action_id,
                        "active_target": dict(self.active_target or {}),
                    },
                )
            )
        try:
            action = self.registry.create(action_name)
        except KeyError as exc:
            self.state = "idle"
            return self._set_result(
                ActionResult(
                    failed=True,
                    reason="unknown_action",
                    detail={"action_name": action_name, "error": str(exc)},
                )
            )
        try:
            from .action_lab import action_definition
            definition = action_definition(action_name)
        except KeyError:
            definition = None
        try:
            normalized_params = definition.merge_and_validate_params(params) if definition else dict(params or {})
        except Exception as exc:
            self.current_action = None
            self.action_name = None
            self.state = "failed"
            return self._set_result(
                ActionResult(
                    failed=True,
                    reason="action_params_invalid",
                    detail={"action_name": action_name, "parameter": str(exc), "validation_error": str(exc)},
                )
            )
        try:
            action.start(normalized_params)
        except Exception as exc:
            self.current_action = None
            self.action_name = None
            self.state = "failed"
            return self._set_result(
                ActionResult(
                    failed=True,
                    reason="action_start_failed",
                    detail={"action_name": action_name, "error": str(exc)},
                )
            )
        self.current_action = action
        self.action_name = action_name
        self.action_id = uuid.uuid4().hex
        self.active_target = self._target_from_params(normalized_params)
        self.state = "running"
        return self._set_result(
            ActionResult(
                reason="action_started",
                detail={
                    "action_name": action_name,
                    "action_id": self.action_id,
                    "requested_target": dict(self.active_target or {}),
                    "active_target": dict(self.active_target or {}),
                },
            )
        )

    def update(self, context: dict[str, Any] | None = None) -> ActionResult:
        if self.state != "running" or self.current_action is None:
            return self._set_result(ActionResult(reason="no_active_action"))
        active_name = self.action_name
        try:
            result = self.current_action.update(context)
        except Exception as exc:
            return self._finish(
                "failed",
                ActionResult(
                    failed=True,
                    reason="action_update_failed",
                    detail={"action_name": active_name, "error": str(exc)},
                ),
            )
        if not isinstance(result, ActionResult):
            return self._finish(
                "failed", ActionResult(failed=True, reason="invalid_action_result")
            )
        if not result.failed:
            try:
                from .action_lab import action_definition
                definition = action_definition(self.action_name or "")
                definition.validate_output(result.output)
                unauthorized = next((effect.action_type for effect in result.effects if effect.action_type not in definition.allowed_effect_types), None)
                if unauthorized is not None:
                    result = ActionResult(
                        failed=True,
                        reason="action_effect_contract_violation",
                        detail={"action_name": self.action_name, "effect_type": unauthorized,
                                "allowed_effect_types": list(definition.allowed_effect_types)},
                    )
            except KeyError:
                pass
            except ValueError as exc:
                result = ActionResult(
                    failed=True,
                    reason="action_output_contract_violation",
                    detail={"action_name": self.action_name, "validation_error": str(exc)},
                )
        if result.failed:
            return self._finish("failed", result)
        if result.done:
            return self._finish("succeeded", result)
        return self._set_result(result)

    def stop(self, *, reason: str = "action_cancelled") -> ActionResult:
        if self.current_action is None:
            return self._set_result(ActionResult(reason="no_active_action"))
        try:
            self.current_action.stop()
        except Exception as exc:
            return self._finish(
                "failed",
                ActionResult(
                    failed=True,
                    reason="action_stop_failed",
                    detail={"action_name": self.action_name, "error": str(exc)},
                ),
            )
        return self._finish(
            "cancelled",
            ActionResult(done=True, reason=reason),
        )

    def reset(self) -> ActionResult:
        reset_error: Exception | None = None
        try:
            if self.current_action is not None:
                self.current_action.reset()
        except Exception as exc:
            reset_error = exc
        finally:
            self.current_action = None
            self.action_name = None
            self.action_id = None
            self.active_target = None
            self.state = "idle"
        if reset_error is not None:
            return self._set_result(
                ActionResult(
                    failed=True,
                    reason="action_reset_failed",
                    detail={"error": str(reset_error)},
                )
            )
        return self._set_result(ActionResult(reason="action_reset"))

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "action_name": self.action_name,
            "action_id": self.action_id,
            "active_target": None if self.active_target is None else dict(self.active_target),
            "running": self.state == "running",
            "last_result": self.last_result.to_dict(),
            "last_terminal": None if self.last_terminal is None else dict(self.last_terminal),
        }

    def _set_result(self, result: ActionResult) -> ActionResult:
        self.last_result = result
        return result

    def _finish(self, terminal_state: str, result: ActionResult) -> ActionResult:
        """Enter a terminal state and always remove the active Action.

        A reset error is recorded for observability but cannot retain a stale
        Action object, key, or target in the active registry.
        """
        if terminal_state not in {"succeeded", "failed", "cancelled"}:
            raise ValueError(f"unsupported terminal state: {terminal_state}")
        action_name = self.action_name
        action_id = self.action_id
        active_target = dict(self.active_target or {})
        detail = dict(result.detail)
        detail.update(
            {
                "action_name": action_name,
                "action_id": action_id,
                "active_target": active_target,
                "lifecycle_state": terminal_state,
            }
        )
        cleanup_error: str | None = None
        try:
            if self.current_action is not None:
                self.current_action.reset()
        except Exception as exc:  # Cleanup must still release the action.
            cleanup_error = str(exc)
            logging.getLogger(__name__).exception(
                "action cleanup failed action=%s id=%s", action_name, action_id
            )
        finally:
            self.current_action = None
            self.action_name = None
            self.action_id = None
            self.active_target = None
            self.state = terminal_state
        if cleanup_error is not None:
            detail["cleanup_error"] = cleanup_error
        terminal_result = ActionResult(
            effects=result.effects,
            done=result.done,
            failed=result.failed,
            reason=result.reason,
            output=result.output,
            detail=detail,
        )
        self.last_terminal = {
            "state": terminal_state,
            "action_name": action_name,
            "action_id": action_id,
            "reason": terminal_result.reason,
            "active_target": active_target,
            "cleanup_error": cleanup_error,
        }
        return self._set_result(terminal_result)

    @staticmethod
    def _target_from_params(params: dict[str, Any] | None) -> dict[str, Any]:
        data = dict(params or {})
        target = data.get("target")
        source = target if isinstance(target, dict) else data
        summary: dict[str, Any] = {}
        for name in ("field_x_m", "field_y_m", "lat", "lon"):
            value = source.get(name)
            if value is None and name == "field_x_m":
                value = source.get("x")
            if value is None and name == "field_y_m":
                value = source.get("y")
            if value is not None:
                summary[name] = value
        altitude = data.get("altitude_m")
        if altitude is not None:
            summary["alt_m"] = altitude
        return summary
