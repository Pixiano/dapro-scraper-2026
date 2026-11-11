"""Phase 4: live link check, no LLM.

dead        404 / 410, or no connection at all (DNS failure, refused) -> dropped
live        2xx / 3xx after redirects
unverified  anything else: 401/403/429 bot walls, 5xx, timeouts. Kept and
            labelled; never counted as live."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from . import UA_PLAIN


HN_ITEM = re.compile(r"^https://news\.ycombinator\.com/item\?id=(\d+)$")
HN_API = "https://hacker-news.firebaseio.com/v0/item/{}.json"


def check(url: str, client: httpx.Client) -> dict:
    # HN answers parallel page fetches with 419; its official item API does not.
    m = HN_ITEM.match(url)
    try:
        if m:
            r = client.get(HN_API.format(m[1]))
            missing = r.status_code == 200 and r.text.strip() == "null"
            code = 404 if missing else r.status_code
        else:
            with client.stream("GET", url) as r:  # body never read
                code = r.status_code
    except (httpx.ConnectError, httpx.UnsupportedProtocol):
        return {"status": "dead", "code": None}
    except Exception as exc:
        return {"status": "unverified", "code": None, "note": type(exc).__name__}
    status = ("live" if 200 <= code < 400 else "dead" if code in (404, 410)
              else "unverified")
    return {"status": status, "code": code}


def check_all(urls: list[str]) -> dict[str, dict]:
    urls = list(dict.fromkeys(u for u in urls if u))
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # ponytail: 16 threads, one shared client; per-host politeness if a site complains
    with httpx.Client(headers={"User-Agent": UA_PLAIN}, timeout=15,
                      follow_redirects=True) as c, ThreadPoolExecutor(16) as ex:
        res = list(ex.map(lambda u: check(u, c), urls))
    return {u: {**r, "checked_at": at} for u, r in zip(urls, res)}
