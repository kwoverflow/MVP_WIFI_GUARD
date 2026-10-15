"""Allowlisted remote presence settings; shared by the API and edge receiver."""
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class PresenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    presence_mv_threshold: float = Field(gt=0, le=100000)
    wander_baseline: float = Field(gt=0, le=100000)
    wander_ratio_threshold: float = Field(ge=1, le=100)
    presence_timeout_s: float = Field(ge=1, le=3600)


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    empty_room_confirmed: StrictBool
