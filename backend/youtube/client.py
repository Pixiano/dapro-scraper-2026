"""Thin authenticated wrapper around the YouTube Data API v3 REST endpoints.

Every call charges the quota ledger first (pessimistic — Google charges even for
most error responses). Errors are surfaced as typed YouTubeError with the
upstream `reason` string; reasons that describe a stable fact about the target
(not found, comments disabled, subscriptions private) are flagged
negative-cacheable."""

import httpx

from .. import quota
from ..config import settings

BASE = "https://www.googleapis.com/youtube/v3"

NEGATIVE_REASONS = {
    "notFound",
    "channelNotFound",
    "videoNotFound",
    "playlistNotFound",
    "commentsDisabled",
    "subscriptionForbidden",
    "captionsNotFound",
}


class YouTubeError(Exception):
    def __init__(self, status: int, reason: str, message: str, negative: bool = False):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.negative = negative


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=20)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _parse_error(r: httpx.Response) -> tuple[str, str]:
    try:
        err = r.json()["error"]
        first = (err.get("errors") or [{}])[0]
        reason = first.get("reason") or f"http{r.status_code}"
        return reason, err.get("message") or r.text[:300]
    except Exception:
        return f"http{r.status_code}", r.text[:300]


async def api_get(resource: str, **params) -> dict:
    if not settings.youtube_api_key:
        raise YouTubeError(
            503, "noApiKey",
            "YOUTUBE_API_KEY is not configured. Copy .env.example to .env and add your key.",
        )
    quota.charge(resource)
    params = {k: v for k, v in params.items() if v is not None}
    params["key"] = settings.youtube_api_key
    try:
        r = await _http().get(f"{BASE}/{resource}", params=params)
    except httpx.HTTPError as exc:
        raise YouTubeError(
            502, "upstreamUnreachable",
            f"Could not reach the YouTube API ({exc.__class__.__name__}).",
        )
    if r.status_code == 200:
        return r.json()
    reason, message = _parse_error(r)
    if reason == "quotaExceeded":
        message = "Google reports the project's daily quota is exhausted upstream."
    raise YouTubeError(r.status_code, reason, message, negative=reason in NEGATIVE_REASONS)
