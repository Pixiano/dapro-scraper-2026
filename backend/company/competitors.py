"""Live competitor discovery: "<category> vs" on Brave + DuckDuckGo (ddgs) together,
"X vs Y" names pulled from result titles, each verified on Wikidata. No LLM.

Confidence = distinct domains whose result titles name the competitor:
>=3 high, 2 medium, 1 low (low -> appendix, not the main list)."""

import re
import time
from urllib.parse import urlparse

import httpx

from ..config import settings
from . import wikidata as wd

BRAVE = "https://api.search.brave.com/res/v1/web/search"
RESULTS = 20          # per engine per query
MAX_NAMES = 15        # candidate names verified per category (bounds Wikidata calls)
AD_HOSTS = ("bing.com", "duckduckgo.com", "googleadservices.com", "doubleclick.net")

VS = re.compile(r"\s+(?:vs\.?|versus|v\.)\s+", re.I)
SEP = re.compile(r"\s+[-–—|]\s+|[:?!()\[\]|,;]")
_last_brave = 0.0


def _brave(query: str, errors: list[str]) -> list[dict]:
    global _last_brave
    if not settings.brave_api_key:
        return []
    # ponytail: free tier is 1 req/s; a global spacer is enough for a sequential CLI
    time.sleep(max(0.0, 1.1 - (time.monotonic() - _last_brave)))
    _last_brave = time.monotonic()
    try:
        r = httpx.get(BRAVE, params={"q": query, "count": RESULTS}, timeout=20,
                      headers={"X-Subscription-Token": settings.brave_api_key,
                               "Accept": "application/json"})
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "snippet": x.get("description", ""), "engine": "brave"}
                for x in (r.json().get("web") or {}).get("results") or []]
    except Exception as exc:
        errors.append(f"brave: {type(exc).__name__}: {exc}")
        return []


def _ddg(query: str, errors: list[str]) -> list[dict]:
    try:
        from ddgs import DDGS
        return [{"title": x.get("title", ""), "url": x.get("href", ""),
                 "snippet": x.get("body", ""), "engine": "ddgs"}
                for x in DDGS().text(query, max_results=RESULTS) or []]
    except Exception as exc:  # DDGSException / RatelimitException / TimeoutException
        errors.append(f"ddgs: {type(exc).__name__}: {exc}")
        return []


def domain(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def web_search(query: str, errors: list[str]) -> list[dict]:
    """Brave + ddgs merged, deduped by URL, ads dropped."""
    seen, out = set(), []
    for r in _brave(query, errors) + _ddg(query, errors):
        d = domain(r["url"])
        if not d or r["url"] in seen or any(d == a or d.endswith("." + a) for a in AD_HOSTS):
            continue
        seen.add(r["url"])
        out.append(r)
    return out


def _is_name_word(w: str) -> bool:
    return any(c.isupper() or c.isdigit() for c in w) or w in ("&", "+")


def _take(words: list[str], from_end: bool) -> str:
    seq = list(reversed(words)) if from_end else words
    picked: list[str] = []
    for w in seq:
        if not _is_name_word(w) or len(picked) == 4:
            break
        picked.append(w)
    if from_end:
        picked.reverse()
    while picked and (picked[0].isdigit() or picked[0] in "&+"):  # "2026", stray "&"
        picked.pop(0)
    while picked and (picked[-1].isdigit() or picked[-1] in "&+"):
        picked.pop()
    return " ".join(picked).strip("\"'“”‘’.")


def names_from_title(title: str) -> list[str]:
    """"Apple Maps vs. Google Maps: I used both" -> ["Apple Maps", "Google Maps"].
    ponytail: capitalisation heuristic; lowercase brands are missed, Wikidata
    verification filters the junk. Upgrade: match against a known-entity list."""
    parts = VS.split(title)
    if len(parts) < 2:
        return []
    out = []
    for i, part in enumerate(parts):
        segs = [s.strip() for s in SEP.split(part) if s.strip()]
        if not segs:
            continue
        seg = segs[-1] if i == 0 else segs[0]
        name = _take(seg.replace("’s", "").replace("'s", "").split(), from_end=(i == 0))
        if name and name not in out:
            out.append(name)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def confidence(n_domains: int) -> str:
    return "high" if n_domains >= 3 else "medium" if n_domains == 2 else "low"


def _verify(names: list[str], own: set[str], own_names: list[str],
            errors: list[str]) -> dict[str, dict]:
    """name -> verified Wikidata entity (enwiki page, not self-owned, not generic)."""
    hits = {n: wd.search(n, errors, prefer_company=False) for n in names}
    ents = wd.get_entities(sorted({q for q in hits.values() if q}), errors)
    out = {}
    for n, q in hits.items():
        e = ents.get(q or "")
        if not e or not wd.enwiki(e) or wd.is_generic(e) or q in own:
            continue
        makers = {*wd.claim_values(e, "P749"), *wd.claim_values(e, "P127"),
                  *wd.claim_values(e, "P176"), *wd.claim_values(e, "P178")}
        if makers & own:  # Waze (parent: Google), Pixel (manufacturer: Google)
            continue
        lbl = _norm(wd.label(e))
        if any(lbl == o or lbl.startswith(o + " ") for o in own_names)                 or _norm(n) in own_names or lbl in own_names:  # "Google Earth", "Bing"
            continue
        out[n] = e
    return out


def for_category(cat: dict, own: set[str], own_names: list[str], per_category: int,
                 errors: list[str]) -> dict:
    x = cat["enwiki"] or cat["label"]
    # ponytail: 3 phrasings x 2 engines; ddgs returns ~8-10 hits per query
    queries = [f"{x} vs", f"vs {x}", f"{x} versus comparison"]
    seen, results = set(), []
    for q in queries:
        for r in web_search(q, errors):
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append(r)
    by_name: dict[str, dict] = {}
    for r in results:
        # snippet sentences carry "X vs Y" too; one domain counts once per name
        found = names_from_title(r["title"])
        for sent in re.split(r"(?<=[.!?])\s+", r.get("snippet") or ""):
            found += [n for n in names_from_title(sent) if n not in found]
        for n in found:
            if _norm(n) in (_norm(cat["label"]), _norm(cat["enwiki"] or "")):
                continue
            slot = by_name.setdefault(n, {"domains": set(), "evidence": []})
            slot["domains"].add(domain(r["url"]))
            if len(slot["evidence"]) < 5:
                slot["evidence"].append({"title": r["title"], "url": r["url"],
                                         "engine": r["engine"]})
    names = sorted(by_name, key=lambda n: -len(by_name[n]["domains"]))[:MAX_NAMES]
    verified = _verify(names, own | {cat["qid"]}, own_names, errors)

    merged: dict[str, dict] = {}  # by QID: "Azure" and "Microsoft Azure" are one
    for n, e in verified.items():
        m = merged.setdefault(e["id"], {
            "qid": e["id"], "label": wd.label(e), "enwiki": wd.enwiki(e),
            "wikidata_url": f"https://www.wikidata.org/wiki/{e['id']}",
            "links": wd.links(e), "matched_names": [], "domains": set(), "evidence": []})
        m["matched_names"].append(n)
        m["domains"] |= by_name[n]["domains"]
        m["evidence"] += [x for x in by_name[n]["evidence"] if x not in m["evidence"]][:5]
    comps = sorted(merged.values(), key=lambda m: -len(m["domains"]))
    for m in comps:
        m["confidence"] = confidence(len(m["domains"]))
        m["domains"] = sorted(m["domains"])
    main = [m for m in comps if m["confidence"] != "low"]
    return {"category": cat["label"], "category_qid": cat["qid"], "queries": queries,
            "results_seen": len(results),
            "engines": sorted({r["engine"] for r in results}),
            "competitors": main[:per_category],
            "appendix": [m for m in comps if m["confidence"] == "low"][:5]}


def overall(per_cat: list[dict], top: int) -> list[dict]:
    """Dedupe across categories; rank by categories competed in, then domains."""
    agg: dict[str, dict] = {}
    for c in per_cat:
        for m in c["competitors"]:
            a = agg.setdefault(m["qid"], {**m, "categories": [], "domains": []})
            a["categories"].append(c["category"])
            a["domains"] = sorted(set(a["domains"]) | set(m["domains"]))
    ranked = sorted(agg.values(), key=lambda a: (-len(a["categories"]), -len(a["domains"])))
    for a in ranked:
        a["confidence"] = confidence(len(a["domains"]))
    return ranked[:top]


def from_names(names: list[str], errors: list[str]) -> list[dict]:
    """--competitors override: Wikidata lookup only, no web search."""
    out: list[dict] = []
    for n in names:
        q = wd.search(n, errors)
        e = wd.get_entities([q], errors).get(q) if q else None
        if not e:
            errors.append(f"competitor {n!r}: not found on Wikidata")
            continue
        out.append({"qid": q, "label": wd.label(e), "enwiki": wd.enwiki(e),
                    "wikidata_url": f"https://www.wikidata.org/wiki/{q}",
                    "links": wd.links(e), "categories": [], "domains": [],
                    "evidence": [], "confidence": "user-supplied"})
    return out


def discover(company: dict, per_category: int = 3, top: int = 10,
             names: list[str] | None = None) -> dict:
    errors: list[str] = []
    if names:
        return {"mode": "user", "per_category": [], "overall": from_names(names, errors),
                "errors": errors}
    own = set(company.get("own_entities") or [])
    own_names = [_norm(x) for x in (company.get("own_names") or []) if x]
    labels = [_norm(x) for x in (company.get("own_labels") or []) if x]
    # "Microsoft Bing" is also just "Bing"
    own_names += labels + [lb[len(o) + 1:] for lb in labels for o in own_names
                           if lb.startswith(o + " ")]
    per_cat = [for_category(c, own, own_names, per_category, errors)
               for c in company.get("categories") or []]
    return {"mode": "search",
            "engines_configured": ["brave"] * bool(settings.brave_api_key) + ["ddgs"],
            "per_category": per_cat, "overall": overall(per_cat, top), "errors": errors}
