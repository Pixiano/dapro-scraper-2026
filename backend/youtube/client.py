"""What is shared about the YouTube Data API v3: its base URL and a typed error
carrying the upstream `reason` string. The calls themselves live in
collectors/youtube.py."""

import httpx

BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeError(Exception):
    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message


def _parse_error(r: httpx.Response) -> tuple[str, str]:
    try:
        err = r.json()["error"]
        first = (err.get("errors") or [{}])[0]
        reason = first.get("reason") or f"http{r.status_code}"
        return reason, err.get("message") or r.text[:300]
    except Exception:
        return f"http{r.status_code}", r.text[:300]
