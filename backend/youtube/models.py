"""Response envelope models (documented in /docs; payloads are raw API JSON)."""

from typing import Any

from pydantic import BaseModel


class QuotaStatus(BaseModel):
    date_pacific: str
    used: int
    budget: int
    soft_stop: int
    remaining: int


class Envelope(BaseModel):
    data: Any
    cached: bool
    fetched_at: str
    quota: QuotaStatus


class ErrorInfo(BaseModel):
    reason: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorInfo
