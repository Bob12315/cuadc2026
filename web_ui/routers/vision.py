from __future__ import annotations

import yaml
from fastapi import APIRouter, HTTPException

from app.config import ROOT_DIR
from web_ui.context import WebContext


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["vision"])
    control = ctx.services.system_control

    @router.post("/localization/clear")
    def clear_localization():
        result = ctx.services.clear_localization()
        ctx.audit.append("LOCALIZATION", "clear", result.ok, result.message)
        return {"ok": result.ok, "message": result.message}

    @router.get("/yolo/stream")
    def stream():
        port = 8081
        try:
            data = yaml.safe_load((ROOT_DIR / "config" / "yolo.yaml").read_text(encoding="utf-8")) or {}
            web_stream = data.get("web_stream", {})
            if isinstance(web_stream, dict):
                port = int(web_stream.get("port", port))
        except (OSError, ValueError, yaml.YAMLError):
            pass
        return {"port": port, "path": "/video/yolo.mjpeg"}

    @router.get("/camera-recording/status")
    def recording_status():
        return {"ok": True, "recording": control.recording_status()}

    @router.post("/camera-recording/toggle")
    def recording_toggle():
        result = control.recording_toggle()
        ctx.audit.append("CAMERA_RECORDING", "toggle", result.ok, result.message)
        return {"ok": result.ok, "message": result.message,
                "recording": control.recording_status()}

    @router.post("/virtual-nadir/reset")
    def reset_virtual_nadir():
        result = control.reset_virtual_nadir()
        ctx.audit.append("VIRTUAL_NADIR", "reset_reference", result.ok, result.message)
        return {"ok": result.ok, "message": result.message}

    @router.post("/yolo/target/{action}")
    def target(action: str, track_id: int | None = None):
        commands = {"unlock": "target unlock", "next": "target next", "prev": "target prev"}
        command = f"target lock {track_id}" if action == "lock" and track_id is not None else commands.get(action)
        if command is None:
            raise HTTPException(status_code=400, detail="invalid target action or missing track_id")
        result = control.target_command(command)
        ctx.audit.append("TARGET", command, result.ok, result.message)
        return {"ok": result.ok, "message": result.message}

    return router
