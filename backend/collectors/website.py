"""WebsiteCollector: the reliable, legitimate core of V2.

Playwright renders the page (JS included), then:
- readability-style text extraction (scripts/styles stripped, main content first)
- full-page screenshot of the main page only
- downloads meaningful images (≥200x200, http, deduped) for the vision stage
- follows a small allowlist of internal links (about/team/contact/...) up to
  settings.site_page_cap pages, depth 1 from the main page.

Text-first by design: the DOM text is the primary content; screenshots feed the
vision stage as a complement."""

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..config import settings
from .base import WEBSITE, SourceArtifact

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SUBPAGE_HINTS = ("about", "team", "bio", "contact", "company", "story",
                 "mission", "service", "product", "press", "who-we-are")

_STRIP_TAGS = ("script", "style", "noscript", "svg", "template", "iframe")


def _norm_url(url: str) -> str:
    return url if "://" in url else "https://" + url


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or "page"


def _canon(url: str) -> str:
    u = urlparse(url)
    return f"{u.netloc.lower().removeprefix('www.')}{u.path.rstrip('/')}"


def extract_text(html: str) -> tuple[str, dict]:
    """Readability pass: (clean text, meta facts) from rendered HTML."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    def meta(**attrs):
        el = soup.find("meta", attrs=attrs)
        return str(el.get("content") or "").strip() if el else ""

    facts = {k: v for k, v in {
        "title": (soup.title.string or "").strip() if soup.title and soup.title.string else "",
        "meta_description": meta(name="description"),
        "og_site_name": meta(property="og:site_name"),
        "og_title": meta(property="og:title"),
        "og_description": meta(property="og:description"),
    }.items() if v}

    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines, prev = [], None
    for raw in root.get_text(separator="\n").splitlines():
        line = raw.strip()
        if line and line != prev:  # drop blanks + consecutive duplicates
            lines.append(line)
            prev = line
    return "\n".join(lines), facts


def subpage_links(html_or_soup, base_url: str) -> list[tuple[str, str]]:
    """Same-host links whose path or anchor text hits the allowlist → [(hint, url)]."""
    soup = (html_or_soup if isinstance(html_or_soup, BeautifulSoup)
            else BeautifulSoup(html_or_soup, "lxml"))
    host = urlparse(base_url).netloc.lower().removeprefix("www.")
    found: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        absu = urljoin(base_url, str(a["href"]).strip()).split("#")[0]
        pu = urlparse(absu)
        if pu.scheme not in ("http", "https"):
            continue
        if pu.netloc.lower().removeprefix("www.") != host:
            continue
        if _canon(absu) == _canon(base_url):
            continue
        hay = (pu.path + " " + a.get_text(" ", strip=True)).lower()
        for kw in SUBPAGE_HINTS:
            if kw in hay:
                found.setdefault(absu, kw)
                break
    return [(kw, u) for u, kw in found.items()]


def _download_images(page, out_dir: Path) -> list[dict]:
    """Pick rendered images ≥200x200 and save up to site_image_cap of them."""
    try:
        metas = page.evaluate(
            "() => Array.from(document.images).map(i => "
            "({src: i.currentSrc || i.src, w: i.naturalWidth, h: i.naturalHeight}))")
    except Exception:
        return []
    picked, seen = [], set()
    for m in metas:
        src = (m.get("src") or "").split("#")[0]
        if not src.startswith("http") or src in seen:
            continue
        if (m.get("w") or 0) < 200 or (m.get("h") or 0) < 200:
            continue
        seen.add(src)
        picked.append(src)
        if len(picked) >= settings.site_image_cap:
            break
    out = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=15,
                      follow_redirects=True) as c:
        for i, src in enumerate(picked):
            try:
                r = c.get(src)
                ct = r.headers.get("content-type", "")
                if r.status_code != 200 or not ct.startswith("image/"):
                    continue
                ext = {"image/png": ".png", "image/webp": ".webp",
                       "image/gif": ".gif"}.get(ct.split(";")[0], ".jpg")
                p = out_dir / f"img_{i}{ext}"
                p.write_bytes(r.content)
                out.append({"url": src, "local_path": str(p)})
            except httpx.HTTPError:
                continue
    return out


def collect(url: str, job_dir: Path) -> SourceArtifact:
    url = _norm_url(url)
    art = SourceArtifact(url=url, platform=WEBSITE, method="playwright")
    shots_dir = job_dir / "screenshots"
    imgs_dir = job_dir / "images"
    shots_dir.mkdir(parents=True, exist_ok=True)
    imgs_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA,
                                      viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            queue: list[tuple[str, str]] = [("main", url)]
            seen = {_canon(url)}
            visited: list[str] = []

            while queue and len(visited) < settings.site_page_cap:
                label, u = queue.pop(0)
                try:
                    page.goto(u, wait_until="domcontentloaded",
                              timeout=settings.site_nav_timeout_ms)
                    page.wait_for_timeout(1200)  # let client-side JS settle
                    html = page.content()
                    text, facts = extract_text(html)
                    if text:
                        art.text_blocks.append(
                            {"label": f"{label}: {u}",
                             "text": text[:settings.site_text_cap]})
                    # Screenshot the main page only: sub-page text is already
                    # captured above, and their full-page screenshots are
                    # near-duplicates of the main page that dominate the cost of
                    # the vision stage for little added information.
                    if label == "main":
                        shot = shots_dir / f"site_{len(visited)}_{_slug(label)}.png"
                        page.screenshot(path=str(shot), full_page=True)
                        art.screenshots.append(str(shot))
                        art.facts.update(facts)
                        art.images = _download_images(page, imgs_dir)
                        for kw, su in subpage_links(html, u):
                            if _canon(su) not in seen:
                                seen.add(_canon(su))
                                queue.append((kw, su))
                    visited.append(u)
                except Exception as exc:
                    art.errors.append(f"{label} {u}: {type(exc).__name__}: {exc}")
            browser.close()
            art.facts["pages_visited"] = visited
    except Exception as exc:
        art.errors.append(f"{type(exc).__name__}: {exc}")

    art.ok = bool(art.text_blocks)
    return art
