"""Bespoke monetization collector: Patreon and Ko-fi / Buy Me a Coffee.

These are the strongest direct evidence of a creator's monetization model —
concrete membership tiers, prices, and patron/supporter counts. Both platforms
are JS-rendered SPAs, so Playwright renders the page and we work off the final
DOM rather than a plain httpx GET.

Patreon embeds a large JSON state blob in the page (Patreon's frontend is
Next.js, so `<script id="__NEXT_DATA__">` is the more likely current shape,
though older/alternate builds have used a `window.patreonBootstrap = {...}`
inline script). That JSON structure is undocumented and drifts over time, so
it is walked defensively — best effort only, wrapped in try/except at every
step. The og:title/og:description fallback (Patreon's og:description commonly
includes a summary and sometimes a patron count) is the baseline signal that
always works even when the JSON shape is unrecognized.

Ko-fi and Buy Me a Coffee are simpler, mostly-static-content pages: og tags
carry the creator name/bio reliably, and a best-effort regex picks up a
visible supporter/member count when present.

Best-effort, public-data-only: no login, no scraping past the public page."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..config import settings
from .base import KOFI, PATREON, SourceArtifact
from .website import UA, extract_text

_SUPPORTER_RE = re.compile(r"([\d,]+)\s+(supporters?|members?|patrons?)", re.I)
_PATRON_RE = re.compile(r"([\d,]+)\s+patrons?", re.I)


def _to_int(v) -> int | None:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _render(url: str) -> str:
    """Render the page with Playwright and return final DOM HTML.

    Module-level and side-effect-free (beyond the browser it spins up) so
    tests can monkeypatch it with canned HTML instead of hitting a network."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_context(
                user_agent=UA, locale="en-US",
                viewport={"width": 1280, "height": 1400}).new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(settings.social_page_wait_ms)
            return page.content()
        finally:
            browser.close()


def _screenshot(url: str, job_dir: Path, art: SourceArtifact) -> None:
    shots = job_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "monetization"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=UA, locale="en-US",
                                       viewport={"width": 1280, "height": 1400}).new_page()
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.social_nav_timeout_ms)
            page.wait_for_timeout(settings.social_page_wait_ms)
            shot = shots / f"{slug}.png"
            page.screenshot(path=str(shot))
            art.screenshots.append(str(shot))
            browser.close()
    except Exception as exc:
        art.errors.append(f"monetization screenshot: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# Patreon
# ---------------------------------------------------------------------------

def _find_json_blobs(html: str) -> list[dict]:
    """Scripts that plausibly hold Patreon's page state, parsed to dicts."""
    soup = BeautifulSoup(html, "lxml")
    blobs: list[dict] = []

    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        try:
            blobs.append(json.loads(next_data.string))
        except (json.JSONDecodeError, TypeError):
            pass

    for tag in soup.find_all("script"):
        s = tag.string or tag.get_text() or ""
        if "patreonBootstrap" not in s:
            continue
        m = re.search(r"window\.patreonBootstrap\s*=\s*(\{.*?\});?\s*(?:</script>|$)", s, re.S)
        if not m:
            continue
        try:
            blobs.append(json.loads(m.group(1)))
        except (json.JSONDecodeError, TypeError):
            pass
    return blobs


def _walk(obj, depth: int = 0):
    """Yield every dict nested anywhere inside obj (bounded depth, best effort)."""
    if depth > 12:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, depth + 1)


_NAME_KEYS = ("full_name", "fullName", "name", "vanity", "creator_name")
_PATRON_KEYS = ("patron_count", "patronCount", "pledge_count", "pledgeCount", "member_count")
_TIER_LIST_KEYS = ("rewards", "tiers")
_TIER_NAME_KEYS = ("title", "name")
_TIER_PRICE_KEYS = ("amount_cents", "amountCents", "amount", "price_cents", "priceCents", "price")
_TIER_DESC_KEYS = ("description", "descriptionText", "summary")


def _extract_tiers_jsonapi(blobs: list[dict]) -> list[dict]:
    """Patreon's real shape (confirmed against a live page): tiers are NOT a
    plain nested list. The campaign uses JSON:API sideloading — reward IDs are
    referenced from `campaign.data.relationships.rewards.data` (just {id,type}
    pointers), and the actual objects with title/amount_cents/description live
    in a separate flat `campaign.included` array, matched by id+type=="reward".
    The generic `_walk` tier-list scan can't see this indirection, so it's
    handled as its own pass."""
    tiers: list[dict] = []
    seen: set[tuple] = set()
    for blob in blobs:
        for node in _walk(blob):
            included = node.get("included")
            if not isinstance(included, list):
                continue
            for item in included:
                if not isinstance(item, dict) or item.get("type") != "reward":
                    continue
                attrs = item.get("attributes") or {}
                title = attrs.get("title")
                if not isinstance(title, str) or not title.strip():
                    continue  # unnamed placeholder entries (e.g. id "-1")
                cents = attrs.get("amount_cents")
                price = "Free" if cents == 0 else (
                    f"${cents / 100:,.2f}" if isinstance(cents, (int, float)) else "")
                desc = attrs.get("description") or ""
                if isinstance(desc, str) and "<" in desc:
                    desc = BeautifulSoup(desc, "lxml").get_text(" ", strip=True)
                key = (title.strip(), price)
                if key in seen:
                    continue
                seen.add(key)
                tiers.append({"name": title.strip(), "price": price,
                              "description": (desc or "").strip()})
    return tiers


def _extract_patreon(blobs: list[dict]) -> dict:
    """Best-effort walk of Patreon's JSON state: name, patron count, tiers."""
    out: dict = {"creator_name": None, "patron_count": None, "tiers": []}
    seen_tier_keys: set[tuple] = set()

    # Try the confirmed real (JSON:API) shape first — most reliable when present.
    jsonapi_tiers = _extract_tiers_jsonapi(blobs)
    if jsonapi_tiers:
        out["tiers"] = jsonapi_tiers
        seen_tier_keys = {(t["name"], t["price"]) for t in jsonapi_tiers}

    for blob in blobs:
        try:
            for node in _walk(blob):
                if out["creator_name"] is None:
                    for k in _NAME_KEYS:
                        v = node.get(k)
                        if isinstance(v, str) and v.strip() and "campaign" not in k.lower():
                            # avoid grabbing unrelated "name" fields too eagerly:
                            # only trust it if the node also looks creator-ish
                            if any(hint in node for hint in
                                   ("patron_count", "patronCount", "creation_name",
                                    "is_nsfw", "url", "vanity")):
                                out["creator_name"] = v.strip()
                                break

                if out["patron_count"] is None:
                    for k in _PATRON_KEYS:
                        if k in node:
                            n = _to_int(node.get(k))
                            if n is not None:
                                out["patron_count"] = n
                                break

                for lk in _TIER_LIST_KEYS:
                    lst = node.get(lk)
                    if not isinstance(lst, list):
                        continue
                    for item in lst:
                        if not isinstance(item, dict):
                            continue
                        try:
                            name = next((v for k in _TIER_NAME_KEYS
                                        if isinstance(v := item.get(k), str) and v.strip()), None)
                            if not name:
                                continue
                            price_raw = next((item.get(k) for k in _TIER_PRICE_KEYS
                                              if item.get(k) is not None), None)
                            price = _format_price(price_raw)
                            desc = next((item.get(k) for k in _TIER_DESC_KEYS
                                        if isinstance(item.get(k), str)), "") or ""
                            key = (name, price)
                            if key in seen_tier_keys:
                                continue
                            seen_tier_keys.add(key)
                            out["tiers"].append({
                                "name": name.strip(),
                                "price": price,
                                "description": desc.strip(),
                            })
                        except Exception:
                            continue
        except Exception:
            continue

    return out


def _format_price(raw) -> str:
    """Normalize a tier price field to a display string like '$5.00'."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    try:
        cents_or_dollars = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    # Patreon typically stores cents (e.g. 500 -> $5.00); treat values that
    # look too large to be plain dollars as cents.
    dollars = cents_or_dollars / 100 if cents_or_dollars >= 100 else cents_or_dollars
    return f"${dollars:,.2f}"


def _collect_patreon(url: str, job_dir: Path, art: SourceArtifact) -> None:
    html = ""
    try:
        html = _render(url)
    except Exception as exc:
        art.errors.append(f"patreon render: {type(exc).__name__}: {exc}")

    tiers: list[dict] = []
    if html:
        try:
            blobs = _find_json_blobs(html)
            parsed = _extract_patreon(blobs) if blobs else {}
            if parsed.get("creator_name"):
                art.facts["creator_name"] = parsed["creator_name"]
            if parsed.get("patron_count") is not None:
                art.facts["patron_count"] = parsed["patron_count"]
            tiers = parsed.get("tiers") or []
        except Exception as exc:
            art.errors.append(f"patreon json parse: {type(exc).__name__}: {exc}")

    # og fallback — always attempted, always the baseline signal.
    og_title = og_desc = ""
    if html:
        try:
            _, facts = extract_text(html)
            og_title = facts.get("og_title", "")
            og_desc = facts.get("og_description", "")
            if og_desc and "patron_count" not in art.facts:
                m = _PATRON_RE.search(og_desc)
                if m:
                    n = _to_int(m.group(1))
                    if n is not None:
                        art.facts["patron_count"] = n
            if og_title and "creator_name" not in art.facts:
                art.facts["creator_name"] = og_title.split("|")[0].strip()
        except Exception as exc:
            art.errors.append(f"patreon og parse: {type(exc).__name__}: {exc}")

    summary = "\n".join(v for v in (og_title, og_desc) if v)
    if summary:
        art.text_blocks.append({"label": "patreon summary", "text": summary})

    if tiers:
        art.facts["tiers"] = tiers
        lines = [f"- {t['name']}: {t['price']} — {t['description']}" for t in tiers]
        art.text_blocks.append({
            "label": f"membership tiers ({len(tiers)})",
            "text": "\n".join(lines),
        })
        art.method = "nextdata"
    else:
        art.facts.setdefault("tiers", [])
        art.method = "og-fallback"

    art.ok = bool(art.text_blocks) or bool(
        {"creator_name", "patron_count"} & set(art.facts)) or bool(tiers)


# ---------------------------------------------------------------------------
# Ko-fi / Buy Me a Coffee
# ---------------------------------------------------------------------------

def _resolved_url(url: str) -> str | None:
    """Cheap plain-HTTP fetch (no browser) to see where a URL actually lands.

    Module-level so tests can monkeypatch it. Returns None on any failure —
    callers must treat that as "unknown", not as evidence of a dead page."""
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=10,
                          follow_redirects=True) as c:
            return str(c.get(url).url)
    except Exception:
        return None


def _is_soft_404(url: str, final_url: str) -> bool:
    """Ko-fi and Buy Me a Coffee redirect unknown creator slugs to their bare
    homepage with HTTP 200 (never a real 404), so a status code can't tell a
    real profile apart from a nonexistent one. Confirmed live: ko-fi.com/<any
    made-up slug> silently 200s to ko-fi.com/. Compare paths instead: a request
    for a specific slug that lands with no path left, on the same host, is the
    homepage bounce, not a profile."""
    orig, final = urlparse(url if "://" in url else "https://" + url), urlparse(final_url)
    orig_host = orig.netloc.lower().removeprefix("www.")
    final_host = final.netloc.lower().removeprefix("www.")
    return bool(orig.path.strip("/")) and not final.path.strip("/") and orig_host == final_host


def _collect_kofi(url: str, job_dir: Path, art: SourceArtifact) -> None:
    site_label = "buymeacoffee" if "buymeacoffee.com" in url.lower() else "kofi"

    final_url = _resolved_url(url)
    if final_url and _is_soft_404(url, final_url):
        art.method = "og"
        art.errors.append(f"{site_label}: no such creator page (redirected to homepage)")
        return

    html = ""
    try:
        html = _render(url)
    except Exception as exc:
        art.errors.append(f"{site_label} render: {type(exc).__name__}: {exc}")

    if html:
        try:
            text, facts = extract_text(html)
            title = facts.get("og_title") or facts.get("title") or ""
            desc = facts.get("og_description") or facts.get("meta_description") or ""
            if title:
                art.facts["creator_name"] = title.strip()
                art.facts["title"] = title.strip()
            if desc:
                art.facts["bio"] = desc.strip()

            summary = "\n".join(v for v in (title, desc) if v)
            if summary:
                art.text_blocks.append({"label": f"{site_label} summary", "text": summary})

            haystack = text or ""
            m = _SUPPORTER_RE.search(haystack)
            if m:
                n = _to_int(m.group(1))
                if n is not None:
                    art.facts["supporter_count"] = n
        except Exception as exc:
            art.errors.append(f"{site_label} parse: {type(exc).__name__}: {exc}")

    art.method = "og"
    art.ok = bool({"creator_name", "bio"} & set(art.facts))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def collect(url: str, job_dir: Path) -> SourceArtifact:
    low = url.lower()
    if "patreon.com" in low:
        art = SourceArtifact(url=url, platform=PATREON)
        try:
            _collect_patreon(url, job_dir, art)
        except Exception as exc:
            art.errors.append(f"patreon: {type(exc).__name__}: {exc}")
    else:
        art = SourceArtifact(url=url, platform=KOFI)
        try:
            _collect_kofi(url, job_dir, art)
        except Exception as exc:
            art.errors.append(f"kofi: {type(exc).__name__}: {exc}")

    _screenshot(url, job_dir, art)
    return art
