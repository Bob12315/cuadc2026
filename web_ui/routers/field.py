from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException

from web_ui.context import WebContext
from web_ui.dto import RuntimeSamplingStartRequest


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["field"])
    services = ctx.services

    def safe(call, *args):
        try:
            return call(*args)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/field-reference/status")
    def reference_status(): return safe(services.field_reference_status)

    @router.post("/field-reference/reset")
    def reference_reset(): return safe(services.field_reference_reset)

    @router.post("/field-reference/freeze")
    def reference_freeze(): return safe(services.field_reference_freeze)

    @router.post("/field-profiles/{profile_id}/runtime-sampling/start")
    def sampling_start(profile_id: str):
        result = safe(services.runtime_sampling_start, profile_id)
        ctx.append_field_audit("runtime_sampling_start", result, profile_id=profile_id)
        return result

    @router.post("/field-reference/runtime-sampling/finalize")
    def sampling_finalize():
        result = safe(services.runtime_sampling_finalize)
        ctx.append_field_audit("runtime_sampling_finalize", result)
        return result

    @router.post("/field-reference/runtime-sampling/cancel")
    def sampling_cancel():
        result = safe(services.runtime_sampling_cancel)
        ctx.append_field_audit("runtime_sampling_cancel", result)
        return result

    @router.post("/field-reference/runtime-sampling/start")
    def competition_sampling(payload: RuntimeSamplingStartRequest):
        lat, lon = payload.forward_marker_lat, payload.forward_marker_lon
        if not math.isfinite(lat) or not math.isfinite(lon):
            raise HTTPException(status_code=400, detail="forward_marker_lat/lon must be finite numbers")
        if not -90.0 <= lat <= 90.0:
            raise HTTPException(status_code=400, detail="forward_marker_lat out of range [-90, 90]")
        if not -180.0 <= lon <= 180.0:
            raise HTTPException(status_code=400, detail="forward_marker_lon out of range [-180, 180]")
        result = services.competition_sampling_start(lat, lon)
        if result.get("ok") is True:
            ctx.append_field_audit("competition_runtime_sampling_start", result)
            return result
        state, error = result.get("state", ""), result.get("error", "unknown error")
        if state != "idle" or "frozen" in str(error).lower():
            raise HTTPException(status_code=409, detail=error)
        if "invalid" in str(error).lower() or "coordinate" in str(error).lower():
            raise HTTPException(status_code=400, detail=error)
        ctx.append_field_audit("competition_runtime_sampling_start", result)
        return result

    @router.post("/field-reference/rtk-heading-sampling/start")
    def rtk_heading_sampling():
        result = services.rtk_heading_sampling_start()
        if result.get("ok") is True:
            ctx.append_field_audit("rtk_heading_sampling_start", result)
            return result
        state, error = result.get("state", ""), result.get("error", "unknown error")
        if state != "idle" or "frozen" in str(error).lower():
            raise HTTPException(status_code=409, detail=error)
        ctx.append_field_audit("rtk_heading_sampling_start", result)
        return result

    @router.get("/field-profiles")
    def profiles(): return safe(services.field_profile_list)

    @router.get("/field-profiles/{profile_id}")
    def profile(profile_id: str): return safe(services.field_profile_get, profile_id)

    @router.get("/field-profiles/{profile_id}/validate")
    def validate(profile_id: str): return safe(services.field_profile_validate, profile_id)

    return router
