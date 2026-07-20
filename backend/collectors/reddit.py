"""Bespoke Reddit collector.

Reddit publishes a JSON view of every public page: append `.json` to a user or
subreddit URL and you get structured data — karma, account age, subscriber
counts, the public description, and recent/top submissions — without auth or
HTML scraping. We read those endpoints directly for facts and text, and use
Playwright only to screenshot the human page for the vision stage.

Fallback: some networks block the .json endpoints outright (403 at IP level,
observed here) while old.reddit.com's plain HTML still serves fine. When the
JSON route yields nothing, we fetch old.reddit.com — httpx first (cheap), and a
Playwright render only if the plain fetch is also blocked — and parse the
classic markup (div.thing titles, sidebar description, subscriber count).

Best-effort and contained: Reddit rate-limits unauthenticated clients hard
(429, sometimes 403), and private/banned/nonexistent targets 404. All of those
become neutral entries in art.errors with ok=False. collect() never raises."""

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..config import settings
from .base import REDDIT, SourceArtifact
from .website import UA

_BASE = "https://www.reddit.com"
_OLD_BASE = "https://old.reddit.com"
_SELFTEXT_EXCERPT = 300


def _parse_target(url: str) -> tuple[str | None, str | None]:
    """(kind, name) where kind is 'user' or 'subreddit'; (None, None) if unclear."""
    path = urlparse(url if "://" in url else "https://" + url).path
    parts = [p for p in path.split("/") if p]
    for i, seg in enumerate(parts):
        low = seg.lower()
        if low in ("user", "u") and i + 1 < len(parts):
            return "user", parts[i + 1]
        if low == "r" and i + 1 < len(parts):
            return "subreddit", parts[i + 1]
    return None, None


def _screenshot(url: str, job_dir: Path, art: SourceArtifact) -> None:
    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "reddit"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=UA, locale="en-US",
                                       viewport={"width": 1280, "height": 1400}).new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(1800)
            shot = shots / f"{slug}.png"
            page.screenshot(path=str(shot))
            art.screenshots.append(str(shot))
            browser.close()
    except Exception as exc:
        art.errors.append(f"reddit screenshot: {type(exc).__name__}")


def _http_note(what: str, status: int) -> str:
    if status == 429:
        return f"reddit {what}: HTTP 429 (rate limited)"
    if status == 403:
        return f"reddit {what}: HTTP 403 (blocked)"
    return f"reddit {what}: HTTP {status}"


def _json_or_none(resp):
    try:
        return resp.json()
    except Exception:
        return None


def _put(art: SourceArtifact, key: str, value) -> None:
    if value not in (None, "", []):
        art.facts[key] = value


def _parse_user_about(art: SourceArtifact, payload: dict) -> None:
    data = (payload or {}).get("data") or {}
    art.facts["reddit_type"] = "user"
    _put(art, "name", data.get("name"))
    _put(art, "link_karma", data.get("link_karma"))
    _put(art, "comment_karma", data.get("comment_karma"))
    _put(art, "created_utc", data.get("created_utc"))
    desc = ((data.get("subreddit") or {}) or {}).get("public_description")
    if desc:
        art.facts["public_description"] = desc
        art.text_blocks.append({"label": "reddit about", "text": desc})


def _parse_sub_about(art: SourceArtifact, payload: dict) -> None:
    data = (payload or {}).get("data") or {}
    art.facts["reddit_type"] = "subreddit"
    _put(art, "display_name", data.get("display_name"))
    _put(art, "subscribers", data.get("subscribers"))
    _put(art, "active_user_count", data.get("active_user_count"))
    desc = data.get("public_description")
    if desc:
        art.facts["public_description"] = desc
        art.text_blocks.append({"label": "subreddit about", "text": desc})


def _parse_posts(art: SourceArtifact, payload: dict) -> None:
    children = ((payload or {}).get("data") or {}).get("children") or []
    lines = []
    for child in children:
        d = (child or {}).get("data") or {}
        title = (d.get("title") or "").strip()
        if not title:
            continue
        line = f"- {title}"
        body = (d.get("selftext") or "").strip()
        if body:
            excerpt = " ".join(body.split())[:_SELFTEXT_EXCERPT]
            line += f"\n  {excerpt}"
        lines.append(line)
    if lines:
        art.text_blocks.append({"label": f"recent posts ({len(lines)})",
                                "text": "\n".join(lines)})


def _render_html(url: str) -> str:
    """Playwright render — separate function so tests can monkeypatch it."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA, locale="en-US").new_page()
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(1500)
            return page.content()
        finally:
            browser.close()


def _fetch_html(url: str) -> tuple[str | None, str | None]:
    """(html, error). Plain httpx first; Playwright render if that's blocked too."""
    err = None
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code == 200 and r.text:
            return r.text, None
        err = f"HTTP {r.status_code}"
    except Exception as exc:
        err = type(exc).__name__
    try:
        return _render_html(url), None
    except Exception as exc:
        return None, f"{err}; playwright: {type(exc).__name__}"


def _to_int(text: str) -> int | None:
    try:
        return int(re.sub(r"[^\d]", "", text or ""))
    except ValueError:
        return None


def _parse_old_html(art: SourceArtifact, kind: str, html: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    titles = []
    for a in soup.select("div.thing a.title")[: settings.reddit_items]:
        t = a.get_text(" ", strip=True)
        if t:
            titles.append(f"- {t}")
    if titles:
        art.text_blocks.append({"label": f"recent posts ({len(titles)})",
                                "text": "\n".join(titles)})
    if kind == "subreddit":
        md = soup.select_one("div.side div.md")
        if md:
            desc = md.get_text("\n", strip=True)
            if desc:
                art.facts.setdefault("public_description", desc[:500])
                art.text_blocks.append({"label": "subreddit about",
                                        "text": desc[:2000]})
        subs = soup.select_one("span.subscribers span.number")
        n = _to_int(subs.get_text()) if subs else None
        if n is not None:
            art.facts.setdefault("subscribers", n)
    else:
        karma = soup.select_one("span.karma")
        n = _to_int(karma.get_text()) if karma else None
        if n is not None:
            art.facts.setdefault("link_karma", n)


def _fallback_old_reddit(art: SourceArtifact, kind: str, name: str) -> None:
    human = f"{_OLD_BASE}/{'user' if kind == 'user' else 'r'}/{name}/"
    html, err = _fetch_html(human)
    if html is None:
        art.errors.append(f"reddit old-html: {err}")
        return
    _parse_old_html(art, kind, html)
    if art.text_blocks or art.facts.keys() & {"subscribers", "link_karma"}:
        art.method = "reddit-json+old-html"
        art.facts["fallback"] = "old.reddit.com"


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=REDDIT, method="reddit-json")
    kind, name = _parse_target(url)
    if not kind or not name:
        art.errors.append("reddit: no user or subreddit in URL")
        _screenshot(url, job_dir, art)
        return art

    art.facts["target"] = f"{'u' if kind == 'user' else 'r'}/{name}"
    if kind == "user":
        about_url = f"{_BASE}/user/{name}/about.json"
        posts_url = f"{_BASE}/user/{name}/submitted.json?limit={settings.reddit_items}"
    else:
        about_url = f"{_BASE}/r/{name}/about.json"
        posts_url = f"{_BASE}/r/{name}/top.json?t=year&limit={settings.reddit_items}"

    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                          follow_redirects=True) as c:
            try:
                r = c.get(about_url)
                if r.status_code != 200:
                    art.errors.append(_http_note("about", r.status_code))
                else:
                    payload = _json_or_none(r)
                    if payload is None:
                        art.errors.append("reddit about: unparseable JSON")
                    elif kind == "user":
                        _parse_user_about(art, payload)
                    else:
                        _parse_sub_about(art, payload)
            except Exception as exc:
                art.errors.append(f"reddit about: {type(exc).__name__}")

            try:
                r = c.get(posts_url)
                if r.status_code != 200:
                    art.errors.append(_http_note("posts", r.status_code))
                else:
                    payload = _json_or_none(r)
                    if payload is None:
                        art.errors.append("reddit posts: unparseable JSON")
                    else:
                        _parse_posts(art, payload)
            except Exception as exc:
                art.errors.append(f"reddit posts: {type(exc).__name__}")
    except Exception as exc:
        art.errors.append(f"reddit: {type(exc).__name__}")

    # If the JSON API gave us nothing (commonly a 403/429 block), try the
    # old.reddit.com HTML, which is often reachable when the API isn't.
    got_json = bool(art.text_blocks) or bool(
        {"name", "display_name", "link_karma", "subscribers"} & set(art.facts))
    if not got_json:
        _fallback_old_reddit(art, kind, name)

    _screenshot(url, job_dir, art)
    art.ok = bool(art.text_blocks) or bool(
        {"name", "display_name", "link_karma", "subscribers"} & set(art.facts))
    return art
