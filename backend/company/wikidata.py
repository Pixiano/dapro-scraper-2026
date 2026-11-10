"""Wikidata + Wikimedia pageviews: resolve a company, its first-party links and
its product/subsidiary categories. Live APIs only, no LLM.

Fail soft: network/API errors are appended to `errors` and the caller gets
whatever was resolved, never an exception."""

import re
from datetime import date
from urllib.parse import quote

import httpx

from . import UA

API = "https://www.wikidata.org/w/api.php"
PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
             "en.wikipedia/all-access/user/{title}/monthly/{start}/{end}")

# property -> (report key, URL template; None = value already a URL)
LINKS = {
    "P856": ("website", None),
    "P2397": ("youtube", "https://www.youtube.com/channel/{}"),
    "P2003": ("instagram", "https://www.instagram.com/{}/"),
    "P2013": ("facebook", "https://www.facebook.com/{}"),
    "P1581": ("blog", None),
    "P3899": ("medium", "https://medium.com/@{}"),
}
SOCIAL = {"P2397", "P2003", "P2013"}
# ponytail: candidate cap keeps pageview calls bounded; raise if categories look thin
MAX_CANDIDATES = 60


def _get(url: str, errors: list[str], **params) -> dict:
    try:
        r = httpx.get(url, params=params or None, headers={"User-Agent": UA},
                      timeout=20, follow_redirects=True)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        errors.append(f"{url.split('/')[2]}: {type(exc).__name__}: {exc}")
        return {}


COMPANY = re.compile(r"\b(company|corporation|conglomerate|organi[sz]ation|enterprise|"
                     r"firm|manufacturer|developer|publisher|studio|brand|startup)\b", re.I)


def search(name: str, errors: list[str]) -> str | None:
    """Top hit, preferring one described as a company ("Netflix" the service
    ranks above Netflix the media company)."""
    data = _get(API, errors, action="wbsearchentities", search=name,
                language="en", type="item", limit=5, format="json")
    hits = data.get("search") or []
    for h in hits:
        if COMPANY.search(h.get("description") or ""):
            return h["id"]
    return hits[0]["id"] if hits else None


def get_entities(ids: list[str], errors: list[str]) -> dict[str, dict]:
    """{qid: entity} with labels, descriptions, claims, sitelinks. 50 ids per call."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        data = _get(API, errors, action="wbgetentities", ids="|".join(ids[i:i + 50]),
                    props="labels|descriptions|claims|sitelinks",
                    languages="en|mul", sitefilter="enwiki", format="json")
        out.update({k: v for k, v in (data.get("entities") or {}).items()
                    if "missing" not in v})
    return out


def claim_values(ent: dict, pid: str) -> list:
    """Values of a property, preferred rank first; deprecated and ended
    (P582 end-time qualifier, e.g. a sold subsidiary) dropped."""
    claims = [c for c in (ent.get("claims") or {}).get(pid, [])
              if c.get("rank") != "deprecated" and "P582" not in (c.get("qualifiers") or {})]
    claims.sort(key=lambda c: c.get("rank") != "preferred")
    vals = []
    for c in claims:
        v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if isinstance(v, dict):
            v = v.get("id") or v.get("time")
        if v:
            vals.append(v)
    return vals


def label(ent: dict) -> str:
    # Wikidata now stores many labels only under "mul" (all languages), e.g. Gmail.
    labels = ent.get("labels") or {}
    return ((labels.get("en") or labels.get("mul") or {}).get("value")
            or ent.get("id", ""))


def enwiki(ent: dict) -> str | None:
    return (ent.get("sitelinks") or {}).get("enwiki", {}).get("title")


def links(ent: dict) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for pid, (key, tmpl) in LINKS.items():
        vals = claim_values(ent, pid)
        out[key] = (tmpl.format(vals[0]) if tmpl else vals[0]) if vals else None
    return out


def _window(today: date) -> tuple[str, str]:
    """The last 3 full months, as pageviews API date strings."""
    m = today.year * 12 + today.month - 1  # current month index
    s, e = m - 3, m - 1
    return (f"{s // 12}{s % 12 + 1:02d}01", f"{e // 12}{e % 12 + 1:02d}28")


def pageviews(title: str, errors: list[str], today: date | None = None) -> int:
    start, end = _window(today or date.today())
    data = _get(PAGEVIEWS.format(title=quote(title.replace(" ", "_"), safe=""),
                                 start=start, end=end), errors)
    return sum(i.get("views", 0) for i in data.get("items") or [])


def _candidates(ents: list[dict]) -> list[str]:
    seen: list[str] = []
    for e in ents:
        for qid in claim_values(e, "P1056") + claim_values(e, "P355"):
            if qid not in seen:
                seen.append(qid)
    return seen[:MAX_CANDIDATES]


def resolve(name: str, top: int = 5, today: date | None = None) -> dict:
    errors: list[str] = []
    out: dict = {"query": name, "qid": None, "errors": errors}
    qid = search(name, errors)
    if not qid:
        errors.append(f"wikidata: no entity found for {name!r}")
        return out
    ent = get_entities([qid], errors).get(qid)
    if not ent:
        return out

    # Candidate categories (products + subsidiaries), with labels/sitelinks/claims.
    cand_ents = get_entities(_candidates([ent]), errors)
    views = {q: pageviews(enwiki(e) or "", errors, today) if enwiki(e) else 0
             for q, e in cand_ents.items()}

    # Holding companies (Alphabet) carry no socials/products: descend into the
    # most-viewed subsidiary (Google) and merge its links + products.
    child = None
    ent_links = links(ent)
    # Socials only: Tesla has no P1056 but is no holding company (was -> SolarCity).
    if not any(ent_links[LINKS[p][0]] for p in SOCIAL):
        subs = [q for q in claim_values(ent, "P355") if q in cand_ents]
        if subs:
            child = cand_ents[max(subs, key=lambda q: views.get(q, 0))]
            for k, v in links(child).items():
                ent_links[k] = ent_links[k] or v
            new = [q for q in _candidates([child]) if q not in cand_ents]
            more = get_entities(new, errors)
            cand_ents.update(more)
            views.update({q: pageviews(enwiki(e) or "", errors, today) if enwiki(e) else 0
                          for q, e in more.items()})

    cats: list[dict] = []
    for q, e in cand_ents.items():
        # ponytail: P279 (subclass-of) without a manufacturer/developer (P176/P178)
        # marks generic classes like "smartphone"; product lines (iPhone, Windows)
        # carry P279 too but name their maker. Heuristic.
        generic = claim_values(e, "P279") and not (claim_values(e, "P176")
                                                   or claim_values(e, "P178"))
        if child and q == child["id"] or not enwiki(e) or generic:
            continue
        cats.append({"qid": q, "label": label(e), "enwiki": enwiki(e),
                     "pageviews": views.get(q, 0)})
    cats.sort(key=lambda c: -c["pageviews"])

    ref_ids = claim_values(ent, "P159")[:1] + claim_values(ent, "P452")[:3]
    refs = get_entities(ref_ids, errors) if ref_ids else {}
    founded = claim_values(ent, "P571")
    # P127 (owned by) is skipped: for public companies it lists shareholders
    # (Vanguard, BlackRock), which must not be filtered out as "self".
    own = {qid, *claim_values(ent, "P355"), *claim_values(ent, "P749")}
    if child:
        own |= {child["id"], *claim_values(child, "P355")}

    out.update({
        "qid": qid,
        "label": label(ent),
        "description": (ent.get("descriptions") or {}).get("en", {}).get("value"),
        "wikidata_url": f"https://www.wikidata.org/wiki/{qid}",
        # "+2007-00-00T..." is year precision -> "2007"
        "founded": founded[0].lstrip("+")[:10].replace("-00", "") if founded else None,
        "headquarters": [label(refs[q]) for q in claim_values(ent, "P159")[:1] if q in refs],
        "industries": [label(refs[q]) for q in claim_values(ent, "P452")[:3] if q in refs],
        "links": ent_links,
        "via_child": {"qid": child["id"], "label": label(child)} if child else None,
        "own_entities": sorted(own),  # used by Phase 2 to drop self-owned "competitors"
        "categories": cats[:top],
    })
    return out
