"""Typed HTTP request payloads for the Web API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictFloat


class ConfigWriteRequest(BaseModel):
    content: str
    action: Literal["save", "reconnect", "restart_app", "restart_yolo"] = "save"


class ActionStartRequest(BaseModel):
    name: str
    params: dict = Field(default_factory=dict)
    authorize: bool = False
    target_source: str | None = None


class RunStartRequest(BaseModel):
    authorize: bool = False
    target_source: str | None = None


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=4096)


class SendControlRequest(BaseModel):
    enabled: bool


class SourceSwitchRequest(BaseModel):
    source: Literal["sitl", "real"]


class ActionMissionStepRequest(BaseModel):
    name: str
    params: dict = Field(default_factory=dict)
    save_as: str | None = None
    label: str | None = None
    on_failed: dict | None = None


class ActionMissionConfigureRequest(BaseModel):
    steps: list[ActionMissionStepRequest]


class ActionMissionConfigureAndStartRequest(ActionMissionConfigureRequest):
    """Configure the editor's Mission and start that exact revision atomically."""

    authorize: bool = False
    target_source: str | None = None


class RuntimeSamplingStartRequest(BaseModel):
    forward_marker_lat: StrictFloat
    forward_marker_lon: StrictFloat
    model_config = {"extra": "forbid"}
