"""Pydantic request models for the harness control plane.

Kept in their own module so ``server.py`` stays a lean route table and the
models can be imported by tests without touching the FastAPI app. All models
forbid extra keys, matching the repo's schema contract style.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

_MAX_MESSAGE_LEN = 32768
_MAX_TITLE_LEN = 200
_MAX_MODEL_LEN = 200


class _ForbidModel(BaseModel, extra="forbid"):
    """Shared base: reject unexpected request fields."""


class ChatRequest(_ForbidModel):
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_LEN)
    session_id: str | None = None
    model: str | None = None


class SessionCreateRequest(_ForbidModel):
    title: str = Field(default="", max_length=_MAX_TITLE_LEN)


class RenameRequest(_ForbidModel):
    title: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)


class SoulToggleRequest(_ForbidModel):
    enabled: bool


class ModelSelectRequest(_ForbidModel):
    model: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)
