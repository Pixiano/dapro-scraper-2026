"""Phase 6 (--social, off by default): Instagram / Facebook profile summaries
from the public page's og:description meta tag. One plain GET per profile, an
honest User-Agent, no login, no posts: profile-level numbers only, never in
the quarter counts. Fail soft: a blocked page is reported, not retried."""

import re

import httpx
from bs4 import BeautifulSoup

from . import UA_PLAIN

# Instagram: "7M Followers, 9 Following, 1,918 Posts - ..."
# Facebook:  "Meta. 10,69,89,335 followers · 406,099 talking about this. ..."
NUM = r"([\d.,]+\s*[KMB]?)"
FIELDS = {"followers": rf"{NUM}\s+followers", "following": rf"{NUM}\s+following",
          "posts": rf"{NUM}\s+posts", "talking_about": rf"{NUM}\s+talking about"}


def parse(desc: str) -> dict:
    return {k: m[1].strip() for k, rx in FIELDS.items()
            if (m := re.search(rx, desc, re.I))}


def profile(url: str) -> dict:
    try:
        r = httpx.get(url, headers={"User-Agent": UA_PLAIN, "Accept-Language": "en-US,en"},
                      timeout=15, follow_redirects=True)
    except Exception as exc:
        return {"url": url, "available": False, "reason": type(exc).__name__}
    tag = BeautifulSoup(r.text, "html.parser").find("meta", property="og:description")
    desc = (tag.get("content") if tag else "") or ""
    nums = parse(str(desc))
    if r.status_code != 200 or not nums:  # login wall / block: no numbers on the page
        return {"url": url, "available": False, "reason": f"HTTP {r.status_code}, no profile data"}
    return {"url": url, "available": True, **nums}


def profiles(links: dict) -> dict:
    return {k: profile(links[k]) for k in ("instagram", "facebook") if links.get(k)}
