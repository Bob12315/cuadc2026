from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from missions.engine import MissionActionStep
from web_ui.context import WebContext
from web_ui.dto import (
    ActionMissionConfigureAndStartRequest,
    ActionMissionConfigureRequest,
    RunStartRequest,
)
from web_ui.templates import ACTION_MISSION_TEMPLATE_NAMES, load_action_mission_template


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter(prefix="/api/action-mission", tags=["missions"])
    mission = ctx.services.mission_control

    @router.get("/status")
    def status():
        try:
            return {"ok": True, "action_mission": mission.action_mission_status_payload()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/templates")
    def templates():
        result = []
        for name, label in ACTION_MISSION_TEMPLATE_NAMES.items():
            data = load_action_mission_template(name)
            result.append({"name": name, "label": label,
                           "path": f"config/action_missions/{name}.json",
                           "description": str(data.get("description") or ""),
                           "step_count": len(data.get("steps") or [])})
        return {"ok": True, "templates": result}

    @router.get("/template/{name}")
    def template(name: str):
        return {"ok": True, "template": load_action_mission_template(name)}

    @router.post("/configure")
    def configure(payload: ActionMissionConfigureRequest):
        try:
            mission.configure_action_mission([
                MissionActionStep(step.name, dict(step.params or {}), save_as=step.save_as,
                                  label=step.label, on_failed=step.on_failed)
                for step in payload.steps
            ])
            return {"ok": True, "action_mission": mission.action_mission_status_payload()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/start")
    def start(payload: RunStartRequest, request: Request):
        source = payload.target_source or mission.active_telemetry_source()
        if source not in {"sitl", "real"}:
            raise HTTPException(status_code=400, detail="target_source must be sitl or real")
        if payload.target_source is not None and source != mission.active_telemetry_source():
            raise HTTPException(status_code=409, detail="target_source is not the active telemetry source")
        try:
            result = mission.action_mission_start(
                authorize=payload.authorize, operator=request.state.identity.operator,
                target_source=source,
            )
            return {"ok": not result.get("failed", False), "action_mission": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/configure-and-start")
    def configure_and_start(payload: ActionMissionConfigureAndStartRequest, request: Request):
        source = payload.target_source or mission.active_telemetry_source()
        if source not in {"sitl", "real"}:
            raise HTTPException(status_code=400, detail="target_source must be sitl or real")
        if payload.target_source is not None and source != mission.active_telemetry_source():
            raise HTTPException(status_code=409, detail="target_source is not the active telemetry source")
        try:
            result = mission.configure_and_start_action_mission(
                [
                    MissionActionStep(
                        step.name,
                        dict(step.params or {}),
                        save_as=step.save_as,
                        label=step.label,
                        on_failed=step.on_failed,
                    )
                    for step in payload.steps
                ],
                authorize=payload.authorize,
                operator=request.state.identity.operator,
                target_source=source,
            )
            return {"ok": not result.get("failed", False), "action_mission": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def transition(name: str):
        try:
            return {"ok": True, "action_mission": getattr(mission, f"action_mission_{name}")()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/stop")
    def stop(): return transition("stop")

    @router.post("/reset")
    def reset(): return transition("reset")

    @router.post("/tick")
    def tick():
        # Compatibility route only.  Request threads are never an execution
        # cadence owner; they can only read the latest published status.
        try:
            payload = mission.action_mission_status_payload()
            return {"ok": True, "deprecated": True, "advanced": False,
                    "action_mission": payload}
        except Exception as exc:
            return {"ok": False, "deprecated": True, "advanced": False,
                    "error": str(exc)}

    @router.post("/skip-current")
    def skip_current():
        try:
            result = mission.action_mission_skip_current()
            ctx.audit.append("ACTION_MISSION", "skip_current", True, result.get("reason", "skip_current"))
            return {"ok": True, "action_mission": result}
        except Exception as exc:
            ctx.audit.append("ACTION_MISSION", "skip_current", False, str(exc))
            return {"ok": False, "error": str(exc)}

    return router
