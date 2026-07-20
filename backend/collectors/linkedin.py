"""Bespoke LinkedIn collector.

LinkedIn embeds rich schema.org JSON-LD in its public, logged-out HTML — far
more than og tags expose: employee count, full address, about text, job titles,
education, recent post text, and published-article headlines. A plain GET returns
it, so we parse JSON-LD directly for the data and use Playwright only to capture a
screenshot for the vision stage.

Best-effort, public-data-only: no login, no scraping past the public profile page.
LinkedIn's ToS restricts automated access and it blocks aggressively, so failures
are expected and contained. For person profiles this stays limited to the public
professional information the page itself already publishes."""

import json
import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..config import settings
from .base import LINKEDIN, SourceArtifact
from .website import UA, extract_text

_FOLLOWERS_RE = re.compile(r"([\d,]+)\s*followers", re.I)
_POST_CAP = 10
_ARTICLE_CAP = 10


def _to_int(v) -> int | None:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _jsonld_items(html: str) -> list[dict]:
    """All JSON-LD objects in the page, @graph flattened."""
    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "@graph" in data:
            data = data["@graph"]
        for it in (data if isinstance(data, list) else [data]):
            if isinstance(it, dict):
                items.append(it)
    return items


def _of_type(items: list[dict], t: str) -> list[dict]:
    def has(it):
        v = it.get("@type")
        return t == v or (isinstance(v, list) and t in v)
    return [it for it in items if has(it)]


def _address(a) -> str | None:
    if not isinstance(a, dict):
        return None
    parts = [a.get(k) for k in ("streetAddress", "addressLocality", "addressRegion",
                                "postalCode", "addressCountry")]
    return ", ".join(str(p) for p in parts if p) or None


def _names(v) -> list[str]:
    out = []
    for x in (v if isinstance(v, list) else [v]):
        if isinstance(x, dict) and x.get("name"):
            out.append(x["name"])
        elif isinstance(x, str):
            out.append(x)
    return out


def _followers_from_stat(item: dict) -> int | None:
    stat = item.get("interactionStatistic")
    if not stat:
        return None
    for s in (stat if isinstance(stat, list) else [stat]):
        itype = s.get("interactionType")
        if isinstance(itype, dict):
            itype = itype.get("@type", "")
        if "Follow" in str(itype):
            return _to_int(s.get("userInteractionCount"))
    return None


def _join(v) -> str | None:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x) or None
    return str(v) if v else None


def _parse_org(art: SourceArtifact, org: dict, og_desc: str) -> None:
    art.facts["linkedin_type"] = "company"
    if org.get("name"):
        art.facts["name"] = org["name"]
    emp = _to_int(org.get("numberOfEmployees"))
    if emp is not None:
        art.facts["employees"] = emp
    hq = _address(org.get("address"))
    if hq:
        art.facts["headquarters"] = hq
    if org.get("slogan"):
        art.facts["slogan"] = org["slogan"]
    same = [u for u in (org.get("sameAs") or []) if isinstance(u, str)]
    if same:
        art.facts["other_links"] = same
    m = _FOLLOWERS_RE.search(og_desc or "")
    if m:
        art.facts["followers"] = _to_int(m.group(1))
    if org.get("description"):
        art.text_blocks.append({"label": f"linkedin about: {org.get('name', '')}",
                                "text": org["description"]})


def _parse_person(art: SourceArtifact, person: dict) -> None:
    art.facts["linkedin_type"] = "person"
    if person.get("name"):
        art.facts["name"] = person["name"]
    title = _join(person.get("jobTitle"))
    if title:
        art.facts["job_title"] = title
    loc = _address(person.get("address"))
    if loc:
        art.facts["location"] = loc
    works = _names(person.get("worksFor"))
    if works:
        art.facts["works_for"] = works
    alma = _names(person.get("alumniOf"))
    if alma:
        art.facts["education"] = alma
    awards = person.get("awards")
    if awards:
        art.facts["awards"] = awards if isinstance(awards, list) else [awards]
    langs = person.get("knowsLanguage")
    if langs:
        art.facts["languages"] = _names(langs) or (langs if isinstance(langs, list) else [langs])
    foll = _followers_from_stat(person)
    if foll is not None:
        art.facts["followers"] = foll
    if person.get("description"):
        art.text_blocks.append({"label": f"linkedin about: {person.get('name', '')}",
                                "text": person["description"]})


def _parse_content(art: SourceArtifact, items: list[dict]) -> None:
    posts = []
    for post in _of_type(items, "DiscussionForumPosting")[:_POST_CAP]:
        txt = (post.get("text") or "").strip()
        if txt:
            posts.append(txt)
    if posts:
        art.text_blocks.append({"label": f"recent posts ({len(posts)})",
                                "text": "\n\n---\n\n".join(posts)})
    heads = []
    for art_it in _of_type(items, "Article")[:_ARTICLE_CAP]:
        h = (art_it.get("headline") or "").strip()
        if h:
            heads.append(h)
    if heads:
        art.text_blocks.append({"label": f"published articles ({len(heads)})",
                                "text": "\n".join(f"- {h}" for h in heads)})


def _screenshot(url: str, job_dir: Path, art: SourceArtifact) -> None:
    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "linkedin"
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
        art.errors.append(f"linkedin screenshot: {type(exc).__name__}")


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=LINKEDIN, method="jsonld+playwright")
    try:
        with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en"},
                          timeout=15, follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            art.errors.append(f"linkedin fetch: HTTP {r.status_code}")
        else:
            items = _jsonld_items(r.text)
            _, facts = extract_text(r.text)
            og_desc = facts.get("og_description") or ""
            orgs, people = _of_type(items, "Organization"), _of_type(items, "Person")
            if people and "/in/" in url:
                _parse_person(art, people[0])
            elif orgs:
                _parse_org(art, orgs[0], og_desc)
            elif people:
                _parse_person(art, people[0])
            else:  # no JSON-LD profile — fall back to og blurb
                blurb = "\n".join(v for v in (facts.get("og_title"), og_desc) if v)
                if blurb:
                    art.text_blocks.append({"label": "linkedin og-summary", "text": blurb})
                art.facts["linkedin_type"] = "company" if "/company/" in url else \
                    "person" if "/in/" in url else "unknown"
            _parse_content(art, items)
    except Exception as exc:
        art.errors.append(f"linkedin: {type(exc).__name__}: {exc}")

    _screenshot(url, job_dir, art)
    art.ok = bool(art.text_blocks) or bool(
        {"name", "followers", "employees"} & set(art.facts))
    return art
