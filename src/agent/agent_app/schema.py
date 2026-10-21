"""Structured output contract between the model and the application layer.

The model fills this schema; the application layer validates every field
before anything reaches a device. ``message`` is an interaction hint only:
for control plans the application replaces it with text built from what the
device control service actually accepted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ActionModel(BaseModel):
    uuid: str = Field(description="Device uuid copied exactly from the device list")
    name: str = Field(default="", description="Device name, for readability only")
    action: str = Field(description="One of the commands listed for this device")
    params: dict[str, int | str] = Field(
        default_factory=dict, description="Command parameters, e.g. {'brightness': 30}"
    )
    params_source: Literal["user", "preference", "default"] = Field(
        default="default",
        description=(
            "'user' only if the user stated the parameter value in this request; "
            "'preference' if taken from the remembered preferences; otherwise 'default'"
        ),
    )


class SkippedModel(BaseModel):
    uuid: str
    name: str = ""
    # A fixed code, not prose: the application shows its own text for each code.
    reason: Literal["already_in_state", "sensitive_action", "not_needed", "other"] = Field(
        default="other",
        description=(
            "Why this device is intentionally left untouched: 'already_in_state' (it already is in the "
            "desired state), 'sensitive_action' (the scene would need an unlock or disarm), 'not_needed', "
            "or 'other'"
        ),
    )


class AliasModel(BaseModel):
    uuid: str
    alias: str


class PlanModel(BaseModel):
    """Plan for one user request."""

    type: Literal["control", "query", "clarify", "chat", "alias"]
    message: str = Field(
        default="",
        description="Short reply in Chinese. For query/clarify/chat this is shown to the user.",
    )
    actions: list[ActionModel] = Field(default_factory=list)
    skipped: list[SkippedModel] = Field(default_factory=list)
    alias: AliasModel | None = None
