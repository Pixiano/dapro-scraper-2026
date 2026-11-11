"""Wikidata + Wikimedia pageviews: resolve a company, its first-party links and
its product/subsidiary categories. Live APIs only, no LLM.

Fail soft: network/API errors are appended to `errors` and the caller gets
whatever was resolved, never an exception."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import quote, urlparse

import httpx

from . import UA, cached

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
    # 30-day cache: entities and monthly pageviews barely move; --fresh bypasses
    return cached("wd:" + url + json.dumps(params, sort_keys=True), "static",
                  lambda: _fetch(url, errors, params))


def _fetch(url: str, errors: list[str], params: dict) -> dict:
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


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def search(name: str, errors: list[str], prefer_company: bool = True,
           exact: bool = False) -> str | None:
    """Top hit; by default prefer one described as a company ("Netflix" the
    service ranks above Netflix the media company). exact: only hits whose
    matched label/alias equals the name ("Teams Worth It" must not -> a film)."""
    data = _get(API, errors, action="wbsearchentities", search=name,
                language="en", type="item", limit=5, format="json")
    hits = [h for h in data.get("search") or [] if not exact
            or _norm((h.get("match") or {}).get("text") or "") == _norm(name)]
    for h in hits if prefer_company else []:
        if COMPANY.search(h.get("description") or ""):
            return h["id"]
    return hits[0]["id"] if hits else None


def by_website(url: str, errors: list[str]) -> str | None:
    """Item whose official website (P856) is this host: pins "Gemini" + gemini.com
    to the exchange, not the constellation. ponytail: 4 spelling variants only."""
    h = urlparse(url if "//" in url else "https://" + url).netloc.lower().removeprefix("www.")
    for u in (f"https://www.{h}", f"https://{h}", f"https://www.{h}/", f"https://{h}/"):
        data = _get(API, errors, action="query", list="search", srlimit=1, format="json",
                    srsearch=f"haswbstatement:P856={u}")
        if hits := (data.get("query") or {}).get("search"):
            return hits[0]["title"]
    return None


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


def is_generic(ent: dict) -> bool:
    """ponytail: P279 (subclass-of) without a manufacturer/developer (P176/P178)
    marks generic classes like "smartphone"; product lines (iPhone, Windows)
    carry P279 too but name their maker. Heuristic."""
    return bool(claim_values(ent, "P279")) and not (claim_values(ent, "P176")
                                                    or claim_values(ent, "P178"))


def base_name(s: str) -> str:
    """"Netflix, Inc." -> "Netflix"."""
    return re.sub(r",?\s+(inc\.?|corp\.?|corporation|ltd\.?|llc|plc|co\.|ag|se|s\.a\.)$",
                  "", s.strip(), flags=re.I)


def _views(ents: dict[str, dict], errors: list[str], today: date | None) -> dict[str, int]:
    # ponytail: 8 threads; Wikimedia allows far more, raise if runs stay slow
    with ThreadPoolExecutor(8) as ex:
        vals = ex.map(lambda e: pageviews(enwiki(e) or "", errors, today) if enwiki(e) else 0,
                      ents.values())
        return dict(zip(ents.keys(), vals))


def _candidates(ents: list[dict]) -> list[str]:
    seen: list[str] = []
    for e in ents:
        for qid in claim_values(e, "P1056") + claim_values(e, "P355"):
            if qid not in seen:
                seen.append(qid)
    return seen[:MAX_CANDIDATES]


def _host(url: str | None) -> str:
    return (urlparse(url or "").netloc or "").lower().removeprefix("www.")


def resolve(name: str, top: int = 5, today: date | None = None,
            url: str | None = None) -> dict:
    """url (optional) pins the company: a Wikidata hit whose official website is
    a different domain is rejected as the wrong entity."""
    errors: list[str] = []
    # Not-on-Wikidata shape (startups): name + website only; Phase 2 then
    # searches the company itself as its own category.
    out: dict = {"query": name, "qid": None, "label": name, "on_wikidata": False,
                 "links": {**{k: None for k, _ in LINKS.values()}, "website": url},
                 "categories": [], "own_entities": [], "own_names": [base_name(name)],
                 "own_labels": [], "errors": errors}
    qid = (by_website(url, errors) if url else None) or search(name, errors)
    ent = get_entities([qid], errors).get(qid) if qid else None
    site = (claim_values(ent, "P856") or [None])[0] if ent else None
    # pinned: an item with no website can't be confirmed (Gemini the constellation)
    if ent and url and _host(site) != _host(url):
        errors.append(f"wikidata: {qid} website {site} != {url}; treated as not on Wikidata")
        ent = None
    if not ent:
        errors.append(f"wikidata: no entity found for {name!r}")
        return out

    # Candidate categories (products + subsidiaries), with labels/sitelinks/claims.
    cand_ents = get_entities(_candidates([ent]), errors)
    views = _views(cand_ents, errors, today)

    # Holding companies (Alphabet) carry no socials/products: descend into the
    # most-viewed subsidiary (Google) and merge its links + products.
    child = None
    ent_links = links(ent)
    # Netflix, Inc. keeps its socials on the same-named service item: borrow them.
    if not any(ent_links[LINKS[p][0]] for p in SOCIAL):
        same = [e for e in cand_ents.values()
                if base_name(label(e)).lower() == base_name(label(ent)).lower()]
        for e in same[:1]:
            for k, v in links(e).items():
                ent_links[k] = ent_links[k] or v
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
            views.update(_views(more, errors, today))

    cats: list[dict] = []
    for q, e in cand_ents.items():
        if child and q == child["id"] or not enwiki(e) or is_generic(e):
            continue
        cats.append({"qid": q, "label": label(e), "enwiki": enwiki(e),
                     "pageviews": views.get(q, 0)})
    cats.sort(key=lambda c: -c["pageviews"])
    if not cats:  # Spotify Technology lists no products: the "Spotify" service item is the category
        q2 = search(name, errors, prefer_company=False)
        e2 = get_entities([q2], errors).get(q2) if q2 and q2 != qid else None
        # only if it is provably theirs: made/owned by them, or the same website
        mine = e2 and (qid in {v for p in ("P127", "P176", "P178", "P749")
                               for v in claim_values(e2, p)}
                       or _host((claim_values(e2, "P856") or [""])[0]) == _host(site) != "")
        if q2 and e2 and mine and enwiki(e2) and not is_generic(e2):
            cand_ents[q2] = e2  # -> own_entities / own_labels too
            cats = [{"qid": q2, "label": label(e2), "enwiki": enwiki(e2), "pageviews": 0}]

    ref_ids = claim_values(ent, "P159")[:1] + claim_values(ent, "P452")[:3]
    refs = get_entities(ref_ids, errors) if ref_ids else {}
    founded = claim_values(ent, "P571")
    # P127 (owned by) is skipped: for public companies it lists shareholders
    # (Vanguard, BlackRock), which must not be filtered out as "self".
    # Own = self, parent, and every product/subsidiary seen (Waze, Gmail...).
    own = {qid, *claim_values(ent, "P749"), *cand_ents}
    if child:
        own.add(child["id"])

    out.update({
        "qid": qid, "on_wikidata": True,
        "label": label(ent),
        "description": (ent.get("descriptions") or {}).get("en", {}).get("value"),
        "wikidata_url": f"https://www.wikidata.org/wiki/{qid}",
        # "+2007-00-00T..." is year precision -> "2007"
        "founded": founded[0].lstrip("+")[:10].replace("-00", "") if founded else None,
        "headquarters": [label(refs[q]) for q in claim_values(ent, "P159")[:1] if q in refs],
        "industries": [label(refs[q]) for q in claim_values(ent, "P452")[:3] if q in refs],
        "links": {**ent_links, "website": url or ent_links["website"]},
        "via_child": {"qid": child["id"], "label": label(child)} if child else None,
        "own_entities": sorted(own),  # Phase 2 drops these as "competitors"
        "own_names": [base_name(label(x)) for x in (ent, child) if x],
        # exact labels of every own product/subsidiary: "Bing" may resolve to a
        # different QID than Microsoft's "Microsoft Bing" item
        "own_labels": sorted({label(e) for e in cand_ents.values()}),
        "categories": cats[:top],
    })
    return out
