"""Offline tests for the company mechanism (no network)."""

from datetime import date

import pytest

from backend.company import wikidata as wd
from backend.config import settings


@pytest.fixture(autouse=True)
def _tmp_cache(monkeypatch, tmp_path):
    """The persistent search/Wikidata cache must never leak between tests or into data/."""
    monkeypatch.setattr(settings, "db_path", tmp_path / "app.db")


def _claim(pid_val, rank="normal", kind="id"):
    v = {"id": pid_val} if kind == "id" else pid_val
    return {"rank": rank, "mainsnak": {"datavalue": {"value": v}}}


def _ent(qid, lbl, enwiki=None, **claims):
    return {"id": qid, "labels": {"en": {"value": lbl}},
            "sitelinks": {"enwiki": {"title": enwiki}} if enwiki else {},
            "claims": claims}


def test_claim_values_rank_order_and_deprecated():
    e = _ent("Q1", "x", P856=[_claim("http://old", "deprecated", "str"),
                              _claim("http://a", "normal", "str"),
                              _claim("http://b", "preferred", "str")])
    assert wd.claim_values(e, "P856") == ["http://b", "http://a"]
    assert wd.claim_values(e, "P999") == []


def test_links_build_urls_and_not_listed():
    e = _ent("Q1", "x", P2397=[_claim("UCabc", kind="str")],
             P856=[_claim("https://x.com", kind="str")])
    l = wd.links(e)
    assert l["youtube"] == "https://www.youtube.com/channel/UCabc"
    assert l["website"] == "https://x.com"
    assert l["instagram"] is None


def test_window_last_three_full_months():
    assert wd._window(date(2026, 11, 10)) == ("20260801", "20261028")
    assert wd._window(date(2026, 1, 5)) == ("20251001", "20251228")


def test_resolve_descends_into_child_and_ranks(monkeypatch):
    ents = {
        "QA": _ent("QA", "Alphabet", P355=[_claim("QG"), _claim("QW")]),
        "QG": _ent("QG", "Google", "Google", P355=[_claim("QY")],
                   P1056=[_claim("QM"), _claim("QS"), _claim("QC")],
                   P2397=[_claim("UCg", kind="str")]),
        "QW": _ent("QW", "Waymo", "Waymo"),
        "QY": _ent("QY", "YouTube", "YouTube"),
        "QM": _ent("QM", "Google Maps", "Google Maps"),
        "QS": _ent("QS", "search engine", "Search engine", P279=[_claim("QX")]),
        "QC": _ent("QC", "no enwiki"),
    }
    views = {"Google": 900, "Waymo": 50, "YouTube": 800, "Google Maps": 300,
             "Search engine": 999}
    monkeypatch.setattr(wd, "search", lambda n, e: "QA")
    monkeypatch.setattr(wd, "get_entities",
                        lambda ids, e: {i: ents[i] for i in ids if i in ents})
    monkeypatch.setattr(wd, "pageviews", lambda t, e, today=None: views.get(t, 0))

    r = wd.resolve("Alphabet", top=5)
    assert r["via_child"] == {"qid": "QG", "label": "Google"}
    assert r["links"]["youtube"] == "https://www.youtube.com/channel/UCg"
    # child itself, the generic class and the no-enwiki item are excluded
    assert [c["label"] for c in r["categories"]] == ["YouTube", "Google Maps", "Waymo"]
    assert {"QA", "QG", "QW", "QY"} <= set(r["own_entities"])


def test_claim_values_drops_ended_and_label_mul_fallback():
    ended = _claim("QOld")
    ended["qualifiers"] = {"P582": [{}]}
    e = _ent("Q1", "x", P355=[ended, _claim("QNow")])
    assert wd.claim_values(e, "P355") == ["QNow"]
    assert wd.label({"id": "Q9", "labels": {"mul": {"value": "Gmail"}}}) == "Gmail"


def test_search_prefers_company_hit(monkeypatch):
    hits = {"search": [{"id": "Q1", "description": "streaming service"},
                       {"id": "Q2", "description": "American media company"}]}
    monkeypatch.setattr(wd, "_get", lambda *a, **k: hits)
    assert wd.search("Netflix", []) == "Q2"
    monkeypatch.setattr(wd, "_get", lambda *a, **k: {"search": hits["search"][:1]})
    assert wd.search("Netflix", []) == "Q1"


def test_resolve_keeps_product_lines_and_skips_shareholders(monkeypatch):
    ents = {
        "QA": _ent("QA", "Apple", P1056=[_claim("QI"), _claim("QP")],
                   P2397=[_claim("UCa", kind="str")], P127=[_claim("QV")],
                   P571=[_claim("+1976-00-00T00:00:00Z", kind="str")]),
        "QI": _ent("QI", "iPhone", "IPhone", P279=[_claim("QP")], P176=[_claim("QA")]),
        "QP": _ent("QP", "smartphone", "Smartphone", P279=[_claim("QX")]),
    }
    monkeypatch.setattr(wd, "search", lambda n, e: "QA")
    monkeypatch.setattr(wd, "get_entities",
                        lambda ids, e: {i: ents[i] for i in ids if i in ents})
    monkeypatch.setattr(wd, "pageviews", lambda t, e, today=None: 1)
    r = wd.resolve("Apple")
    assert [c["label"] for c in r["categories"]] == ["iPhone"]
    assert "QV" not in r["own_entities"]
    assert r["founded"] == "1976"
    assert r["via_child"] is None  # has socials -> not a holding company


def test_resolve_no_products_uses_same_named_service(monkeypatch):
    ents = {"QC": _ent("QC", "Spotify Technology", P2397=[_claim("UCs", kind="str")]),
            "QS": _ent("QS", "Spotify", "Spotify", P178=[_claim("QC")]),
            "QX": _ent("QX", "Gemini", "Gemini (constellation)")}
    pick = {"Spotify": "QS", "Gemini": "QX"}
    monkeypatch.setattr(wd, "search", lambda n, e, prefer_company=True: "QC" if prefer_company else pick[n])
    monkeypatch.setattr(wd, "get_entities", lambda ids, e: {i: ents[i] for i in ids if i in ents})
    r = wd.resolve("Spotify")
    assert [c["qid"] for c in r["categories"]] == ["QS"] and "QS" in r["own_entities"]
    assert wd.resolve("Gemini")["categories"] == []  # not theirs: the constellation stays out


def test_resolve_not_found(monkeypatch):
    monkeypatch.setattr(wd, "search", lambda n, e: None)
    r = wd.resolve("Nope Corp")
    assert r["qid"] is None and r["errors"]


# ---- Phase 2: competitors ----

from backend.company import competitors as cp  # noqa: E402


def test_names_from_title():
    f = cp.names_from_title
    assert f("Apple Maps vs. Google Maps: I used both navigation apps") == ["Apple Maps", "Google Maps"]
    assert f("Google Maps vs Waze vs Apple Maps — which is best in 2026?") == ["Google Maps", "Waze", "Apple Maps"]
    assert f("Why I switched from Google Maps vs OpenStreetMap") == ["Google Maps", "OpenStreetMap"]
    assert f("AWS vs Azure 2026 | Comparison") == ["AWS", "Azure"]
    assert f("I tested 6 Google Maps alternatives") == []


def test_confidence_buckets():
    assert [cp.confidence(n) for n in (5, 3, 2, 1)] == ["high", "high", "medium", "low"]


def test_web_search_merges_dedupes_and_drops_ads(monkeypatch):
    monkeypatch.setattr(cp, "_brave", lambda q, e: [
        {"title": "A vs B", "url": "https://x.com/1", "engine": "brave"},
        {"title": "ad", "url": "https://www.bing.com/aclick?x", "engine": "brave"}])
    monkeypatch.setattr(cp, "_ddg", lambda q, e: [
        {"title": "A vs B", "url": "https://x.com/1", "engine": "ddgs"},
        {"title": "A vs C", "url": "https://y.org/2", "engine": "ddgs"}])
    assert [r["url"] for r in cp.web_search("q", [])] == ["https://x.com/1", "https://y.org/2"]


def test_for_category_filters_self_owned_and_ranks(monkeypatch):
    titles = [("Google Maps vs Apple Maps", "a.com"), ("Apple Maps vs Google Maps", "b.com"),
              ("Google Maps vs Apple Maps vs Waze", "c.com"), ("Google Maps vs Waze", "d.com"),
              ("Google Maps vs Here WeGo", "e.com"), ("Google Maps vs Google Earth", "f.com")]
    monkeypatch.setattr(cp, "web_search", lambda q, e: [
        {"title": t, "url": f"https://{d}/x", "engine": "ddgs"} for t, d in titles])
    qids = {"Apple Maps": "QAM", "Waze": "QWZ", "Here WeGo": "QH", "Google Earth": "QGE"}
    ents = {"QAM": _ent("QAM", "Apple Maps", "Apple Maps"),
            "QWZ": _ent("QWZ", "Waze", "Waze", P749=[_claim("QG")]),
            "QH": _ent("QH", "Here WeGo", "Here WeGo"),
            "QGE": _ent("QGE", "Google Earth", "Google Earth")}
    monkeypatch.setattr(wd, "search", lambda n, e, **k: qids.get(n))
    monkeypatch.setattr(wd, "get_entities", lambda ids, e: {i: ents[i] for i in ids if i in ents})
    cat = {"qid": "QM", "label": "Google Maps", "enwiki": "Google Maps"}
    r = cp.for_category(cat, {"QG"}, ["google"], 3, [])
    assert [(c["label"], c["confidence"]) for c in r["competitors"]] == [("Apple Maps", "high")]
    assert [c["label"] for c in r["appendix"]] == ["Here WeGo"]  # 1 domain -> low
    # Waze (parent Google) and Google Earth (own name) never appear


def test_overall_dedupes_across_categories():
    m = lambda q, d: {"qid": q, "label": q, "domains": d, "competitors": []}  # noqa: E731
    per = [{"category": "Cloud", "competitors": [m("MS", ["a", "b"]), m("AWS", ["a", "b", "c"])]},
           {"category": "Search", "competitors": [m("MS", ["c", "d"])]}]
    r = cp.overall(per, 10)
    assert [(x["qid"], x["categories"], x["confidence"]) for x in r] == [
        ("MS", ["Cloud", "Search"], "high"), ("AWS", ["Cloud"], "high")]


def test_own_product_label_filtered_even_with_other_qid(monkeypatch):
    monkeypatch.setattr(cp, "web_search", lambda q, e: [
        {"title": "Google vs Bing", "url": f"https://{d}/x", "engine": "ddgs"} for d in "abc"])
    monkeypatch.setattr(wd, "search", lambda n, e, **k: {"Bing": "QB2", "Google": "QG"}[n])
    ents = {"QB2": _ent("QB2", "Bing", "Bing"), "QG": _ent("QG", "Google", "Google")}
    monkeypatch.setattr(wd, "get_entities", lambda ids, e: {i: ents[i] for i in ids if i in ents})
    cat = {"qid": "QB1", "label": "Microsoft Bing", "enwiki": "Microsoft Bing"}
    r = cp.for_category(cat, {"QB1"}, ["microsoft", "microsoft bing", "bing"], 3, [])
    assert [c["label"] for c in r["competitors"]] == ["Google"]


def test_discover_strips_company_prefix_from_own_labels(monkeypatch):
    seen = {}
    monkeypatch.setattr(cp, "for_category",
                        lambda cat, own, names, n, e: seen.setdefault("names", names) and {
                            "category": "x", "competitors": []})
    cp.discover({"categories": [{"qid": "Q"}], "own_names": ["Microsoft"],
                 "own_labels": ["Microsoft Bing", "Xbox"]})
    assert {"microsoft", "microsoft bing", "bing", "xbox"} <= set(seen["names"])


# ---- Phase 2b: startups / not on Wikidata ----

def test_names_from_list_snippets():
    f = cp.names_from_list
    assert f("The best Adaption Labs alternatives are Miro, Navan (Formerly TripActions), and Autodesk Forma") \
        == ["Miro", "Navan", "Autodesk Forma"]
    assert f("Compare the top Multiverse Computing alternatives — OpenAI, Cursor by Anysphere and GrubMarket") \
        == ["OpenAI", "Cursor", "GrubMarket"]
    assert f("Find top-ranking free apps similar to X for your needs") == []


def test_unverified_names_need_two_domains(monkeypatch):
    monkeypatch.setattr(cp, "web_search", lambda q, e: [
        {"title": "Top Acme alternatives", "url": f"https://{d}/x", "engine": "ddgs",
         "snippet": s} for d, s in [("a.com", "Acme competitors include Zeta Labs and Omni."),
                                    ("b.com", "Acme competitors: Zeta Labs, Kappa.")]])
    monkeypatch.setattr(wd, "search", lambda n, e, **k: None)  # none on Wikidata
    monkeypatch.setattr(wd, "get_entities", lambda ids, e: {})
    r = cp.for_category({"qid": None, "label": "Acme", "enwiki": None, "self": True},
                        set(), ["acme"], 3, [])
    # Zeta Labs: 2 domains -> medium, downgraded to low (not on Wikidata) -> appendix
    assert r["competitors"] == []
    assert [(c["label"], c["on_wikidata"], c["confidence"]) for c in r["appendix"]] == [
        ("Zeta Labs", False, "low")]
    assert r["queries"] == ['"Acme" vs', '"Acme" competitors', '"Acme" alternatives']


def test_discover_falls_back_to_self_category(monkeypatch):
    got = {}
    monkeypatch.setattr(cp, "for_category", lambda cat, *a: got.setdefault("cat", cat) and {
        "category": cat["label"], "competitors": []})
    r = cp.discover({"query": "Acme", "label": "Acme", "qid": None, "categories": []})
    assert got["cat"]["self"] is True and r["mode"] == "search-self"


def test_resolve_url_mismatch_rejects_entity(monkeypatch):
    ents = {"Q1": _ent("Q1", "Adaptation", P856=[_claim("https://other.org", kind="str")]),
            "Q2": _ent("Q2", "Gemini")}  # no website: can't be confirmed
    monkeypatch.setattr(wd, "by_website", lambda u, e: None)
    monkeypatch.setattr(wd, "search", lambda n, e, **k: {"Adaption": "Q1", "Gemini": "Q2"}[n])
    monkeypatch.setattr(wd, "get_entities", lambda ids, e: {i: ents[i] for i in ids if i in ents})
    r = wd.resolve("Adaption", url="https://www.adaption.ai")
    assert r["qid"] is None and r["on_wikidata"] is False
    assert r["links"]["website"] == "https://www.adaption.ai" and r["label"] == "Adaption"
    assert wd.resolve("Gemini", url="https://www.gemini.com")["qid"] is None


def test_by_website_tries_spellings(monkeypatch):
    seen = []
    monkeypatch.setattr(wd, "_get", lambda url, e, **p: seen.append(p["srsearch"]) or (
        {"query": {"search": [{"title": "QG"}]}} if p["srsearch"].endswith("//gemini.com") else {}))
    assert wd.by_website("gemini.com/", []) == "QG" and len(seen) == 2


# ---- Phase 3: collect ----

from backend.company import collect as co  # noqa: E402


def test_quarter_bounds_and_year_edge():
    b = co.quarter_bounds(date(2026, 11, 11))
    assert [(l, s.isoformat(), e.isoformat()) for l, s, e in b] == [
        ("Q2 2026", "2026-04-01", "2026-07-01"), ("Q3 2026", "2026-07-01", "2026-10-01"),
        ("Q4 2026 (to date)", "2026-10-01", "2026-11-12")]
    b = co.quarter_bounds(date(2026, 2, 3))
    assert [l for l, _, _ in b] == ["Q3 2025", "Q4 2025", "Q1 2026 (to date)"]
    assert co.bucket(date(2026, 9, 30), co.quarter_bounds(date(2026, 11, 11))) == "Q3 2026"
    assert co.bucket(date(2026, 3, 31), co.quarter_bounds(date(2026, 11, 11))) is None


def test_parse_date_formats():
    assert co._parse_date("Tue, 05 Aug 2026 10:00:00 GMT") == date(2026, 8, 5)
    assert co._parse_date("2026-08-05T10:00:00Z") == date(2026, 8, 5)
    assert co._parse_date("2026-08-05T10:00:00.000+00:00") == date(2026, 8, 5)
    assert co._parse_date("garbage") is None and co._parse_date(None) is None


def test_names_entity_word_boundary():
    assert co.names_entity("Apple launches new AI model", "Apple")
    assert not co.names_entity("Pineapple prices soar", "Apple")
    assert co.names_entity("Why Disney+ raised prices", "Disney+")


def test_feed_items_rss_and_atom():
    rss = ("<rss><channel><item><title>A</title><link>https://x/a</link>"
           "<pubDate>Tue, 05 Aug 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title>'
            '<link href="https://x/b"/><published>2026-08-06T00:00:00Z</published></entry></feed>')
    assert co.feed_items(rss) == [{"title": "A", "link": "https://x/a",
                                   "published": "Tue, 05 Aug 2026 10:00:00 GMT"}]
    assert co.feed_items(atom) == [{"title": "B", "link": "https://x/b",
                                    "published": "2026-08-06T00:00:00Z"}]


def test_hacker_news_filters_title_and_buckets(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"hits": [
            {"title": "OpenAI ships X", "objectID": "1", "created_at": "2026-08-01T00:00:00Z", "points": 99},
            {"title": "Unrelated", "objectID": "2", "created_at": "2026-08-01T00:00:00Z", "points": 99},
            {"title": "OpenAI old", "objectID": "3", "created_at": "2025-01-01T00:00:00Z", "points": 99}]}
    monkeypatch.setattr(co, "_http", lambda url, **p: R())
    items = co.hacker_news("OpenAI", co.quarter_bounds(date(2026, 11, 11)), [])
    assert [(i["title"], i["quarter"], i["url"]) for i in items] == [
        ("OpenAI ships X", "Q3 2026", "https://news.ycombinator.com/item?id=1")]


def test_months_split():
    m = co.months(date(2026, 4, 1), date(2026, 6, 15))
    assert [(a.isoformat(), b.isoformat()) for a, b in m] == [
        ("2026-04-01", "2026-05-01"), ("2026-05-01", "2026-06-01"), ("2026-06-01", "2026-06-15")]
    assert len(co.months(date(2025, 12, 1), date(2026, 2, 1))) == 2  # year edge


# ---- phase 4: report ----

def test_ai_terms_strips_entity_name():
    from backend.company.report import ai_terms
    assert ai_terms("Mistral AI raises funding", "Mistral AI") == []
    assert ai_terms("OpenAI ships new office", "OpenAI") == []
    assert ai_terms("Mistral AI launches reasoning model and AI agents", "Mistral AI") == \
        ["reasoning model", "ai agents"]
    assert ai_terms("Meta trains Llama on GPUs", "Meta") == ["llama", "gpus"]
    assert ai_terms("Said to be detailed", None) == []  # "ai" inside words is not AI


def test_outlook_run_rate():
    from backend.company.report import outlook, quarter_end
    assert quarter_end(date(2026, 10, 1)) == date(2027, 1, 1)
    o = outlook(last=40, to_date=23, q_start=date(2026, 10, 1), today=date(2026, 11, 15))
    assert o["elapsed_days"] == 46 and o["quarter_days"] == 92
    assert o["projection"] == 46 and o["trend"] == "flat"
    assert outlook(10, 20, date(2026, 10, 1), date(2026, 11, 15))["trend"] == "up"
    assert outlook(0, 0, date(2026, 10, 1), date(2026, 10, 2))["trend"] == "none"


def test_top_round_robin():
    from backend.company.report import top
    ai = [{"source": "google_news", "quarter": "Q", "published": f"2026-08-{d:02d}",
           "url": str(d)} for d in range(1, 10)]
    ai += [{"source": "hacker_news", "quarter": "Q", "published": "2026-08-01",
            "url": "h1", "points": 50}, {"source": "hacker_news", "quarter": "Q",
                                         "published": "2026-08-02", "url": "h2", "points": 900}]
    assert [i["url"] for i in top(ai, "Q", 4)] == ["h2", "09", "h1", "08"] or \
        [i["url"] for i in top(ai, "Q", 4)] == ["h2", "9", "h1", "8"]


def test_linkcheck_status_rules():
    import httpx
    from backend.company.linkcheck import check

    def handler(req):
        if req.url.host == "down.test":
            raise httpx.ConnectError("dns")
        if req.url.host == "hacker-news.firebaseio.com":
            return httpx.Response(200, text="null" if "/1.json" in req.url.path else "{\"id\": 2}")
        return httpx.Response({"gone": 404, "wall": 403, "ok": 200, "err": 503}[req.url.host])
    c = httpx.Client(transport=httpx.MockTransport(handler))
    st = {h: check(f"https://{h}/", c)["status"] for h in ("gone", "wall", "ok", "err", "down.test")}
    assert st == {"gone": "dead", "wall": "unverified", "ok": "live", "err": "unverified",
                  "down.test": "dead"}
    hn = "https://news.ycombinator.com/item?id="
    assert check(hn + "1", c)["status"] == "dead" and check(hn + "2", c)["status"] == "live"


def test_build_report_pdf(tmp_path, monkeypatch):
    from backend.company import linkcheck, report
    monkeypatch.setattr(linkcheck, "check_all",
                        lambda urls: {u: {"status": "dead" if "dead" in u else "live",
                                          "code": 200, "checked_at": "t"} for u in urls})
    q = [{"label": "Q2 2026", "start": "2026-04-01", "end": "2026-07-01"},
         {"label": "Q3 2026", "start": "2026-07-01", "end": "2026-10-01"},
         {"label": "Q4 2026 (to date)", "start": "2026-10-01", "end": "2026-11-12"}]
    it = lambda t, u, qq: {"source": "hacker_news", "title": t, "url": u,  # noqa: E731
                           "published": "2026-08-01", "quarter": qq, "points": 30}
    ent = lambda n, items: {"label": n, "searched_as": n, "links": {}, "items": items,  # noqa
                            "errors": []}
    raw = {"company": {"label": "Acme", "wikidata_url": None},
           "competitors": {"mode": "user", "overall": [{"label": "Rival", "evidence": []}]},
           "collected": {"quarters": q, "company": ent("Acme", [it("Acme AI chip", "u1", "Q3 2026")]),
                         "competitors": [ent("Rival", [it("Rival LLM [beta]", "u2", "Q3 2026"),
                                                       it("Rival AI", "dead3", "Q3 2026"),
                                                       it("Rival lunch", "u4", "Q3 2026")])]}}
    out = report.build(raw, tmp_path)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert out["pdf"].exists() and "[Rival LLM beta](u2)" in md and "dead3" not in md
    assert "Q3 2026 1/3" in md  # "Rival AI" is the name, not AI news


# ---- phase 2-4 review fixes ----

def test_review_fixes():
    from backend.company.collect import _parse_date, feed_items
    from backend.company.report import ai_terms
    from backend.pdf import _inline
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>t</title>'
            '<link rel="replies" href="https://b/c#comments"/><link rel="alternate" href="/c"/>'
            '<updated>2026-11-01T00:00:00Z</updated><published>2026-08-01T00:00:00Z</published>'
            '</entry></feed>')
    it = feed_items(atom, "https://b/feed")[0]
    assert it["link"] == "https://b/c" and it["published"].startswith("2026-08-01")
    assert _parse_date("Tue, 10 Nov 2026 23:30:00 -0800") == date(2026, 11, 11)
    assert ai_terms("AI-powered search and Gen-AI push", "X") == ["ai", "ai"]
    assert ai_terms("Mistral AI raises", "Mistral", "Mistral AI") == []
    assert 'href="https://en.wikipedia.org/wiki/Foo_(bar)"' in _inline(
        "[x](https://en.wikipedia.org/wiki/Foo_(bar))")
    assert "%22" in _inline('[x](https://a.b/q?"x")')


# ---- phase 6/7 ----

def test_social_parse():
    from backend.company.social import parse
    assert parse("7M Followers, 9 Following, 1,918 Posts - See Instagram photos") == \
        {"followers": "7M", "following": "9", "posts": "1,918"}
    assert parse("Meta. 106,989,335 followers · 406,099 talking about this.") == \
        {"followers": "106,989,335", "talking_about": "406,099"}
    assert parse("Log in to Facebook") == {}


def test_company_web_routes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.company import web
    monkeypatch.setattr(web, "OUT", tmp_path)
    (tmp_path / "acme" / "2026-11-11").mkdir(parents=True)
    (tmp_path / "acme" / "2026-11-11" / "report.pdf").write_bytes(b"%PDF-1.4")
    app = FastAPI()
    app.include_router(web.router)
    c = TestClient(app)
    assert c.get("/api/company/reports").json()[0]["report"] == "acme/2026-11-11"
    assert c.get("/api/company/reports/acme/2026-11-11/report.pdf").content == b"%PDF-1.4"
    assert c.get("/api/company/reports/acme/2026-11-11/raw.json").status_code == 400
    assert c.get("/api/company/reports/..%2F..%2Fx/2026-11-11/report.pdf").status_code in (400, 404)
    assert c.post("/api/company/runs", json={"name": "  "}).status_code == 400
    assert c.get("/api/company/runs/nope").status_code == 404


def test_vision_notes_fail_soft(tmp_path, monkeypatch):
    from backend.company import visual

    def fake_shot(url, path):
        if "blocked" in url:
            return "TimeoutError: page"
        from PIL import Image
        img = Image.new("RGB", (40, 40), "white")
        img.paste((0, 0, 0), (0, 0, 20, 40))  # half black: not blank
        img.save(path)
        return None
    monkeypatch.setattr(visual, "screenshot", fake_shot)
    monkeypatch.setattr(visual.client, "describe_image",
                        lambda p, prompt, max_tokens: "- Acme GPT\nPOSITIONING: AI first")
    ents = [{"label": "Acme", "links": {"website": "https://acme.test"}},
            {"label": "Blocked", "links": {"website": "https://blocked.test"}},
            {"label": "NoSite", "links": {}}]
    a, b, c = visual.notes(ents, tmp_path)
    assert a["text"].startswith("- Acme GPT") and a["error"] is None
    assert b["error"].startswith("TimeoutError") and "text" not in b
    assert c["error"] == "no website"


def test_vision_line_filter():
    from backend.company.visual import keep
    assert not keep("Accept all cookies") and not keep("- Research") and not keep("Learn more") and not keep("  ")
    assert keep("- Introducing Claude Opus 5.5") and keep("Try ChatGPT")
    assert keep("Sovereign AI") and keep("POSITIONING: an AI lab")


# ---- improvements: cache, name cleanup, same-class/owner, ambiguous AI, startups ----

def test_search_cache_skips_errors_and_fresh(monkeypatch):
    import backend.company as pkg
    calls = []

    def ok(q, e):
        calls.append(q)
        return [{"title": "A vs B", "url": "https://x.com/1", "engine": "ddgs"}]

    def throttled(q, e):
        calls.append(q)
        e.append("ddgs: throttled")
        return [{"title": "t", "url": "https://y.com/1", "engine": "ddgs"}]
    monkeypatch.setattr(cp, "_brave", lambda q, e: [])
    monkeypatch.setattr(cp, "_ddg", ok)
    cp.web_search("q", [])
    assert cp.web_search("q", [])[0]["url"] == "https://x.com/1" and calls == ["q"]  # cached
    monkeypatch.setattr(pkg, "FRESH", True)
    cp.web_search("q", [])
    assert calls == ["q", "q"]  # --fresh bypasses
    monkeypatch.setattr(pkg, "FRESH", False)
    monkeypatch.setattr(cp, "_ddg", throttled)
    cp.web_search("t", [])
    cp.web_search("t", [])
    assert calls.count("t") == 2  # errored half-results are never cached


def test_title_case_junk_trimmed_and_exact_wikidata(monkeypatch):
    f = cp.names_from_title
    assert f("The Gemini vs ChatGPT Showdown") == ["Gemini", "ChatGPT"]
    assert f("Is Zoom vs Teams Worth It") == ["Zoom", "Teams"]
    assert f("Best Netflix vs Disney+ Comparison") == ["Netflix", "Disney+"]
    monkeypatch.setattr(wd, "_get", lambda *a, **k: {"search": [
        {"id": "QF", "match": {"text": "Teams Worth It (film)"}},
        {"id": "QT", "match": {"text": "Teams"}}]})
    assert wd.search("Teams", [], prefer_company=False, exact=True) == "QT"
    assert wd.search("Worth", [], exact=True) is None
    monkeypatch.setattr(wd, "search", lambda n, e, **k: {"ChatGPT": "QC"}.get(n))
    assert cp._lookup("ChatGPT Showdown", []) == "QC"  # outer word dropped


def test_off_class_demoted_and_same_owner_merged():
    c = lambda q, l, d, owners=(), off=False: {  # noqa: E731, E741
        "qid": q, "label": l, "domains": d, "owners": list(owners), "off_class": off}
    per = [{"category": "Video", "competitors": [
        c("QP", "Prime Video", ["a", "b", "c"], ["QA"]),
        c("QL", "Lifewire", ["a", "b", "c"], off=True),
        c("QAP", "Amazon Prime", ["d"], ["QA"])]}]
    r = cp.overall(per, 10)
    assert [(x["label"], x.get("also"), len(x["domains"]), x["confidence"]) for x in r] == [
        ("Prime Video", ["Amazon Prime"], 4, "high"), ("Lifewire", None, 3, "medium")]


def test_ambiguous_ai_terms_need_context():
    from backend.company.report import ai_terms
    assert ai_terms("Gemini exchange adds Solana staking", "X") == []
    assert ai_terms("Claude Monet exhibition opens in Paris", "X") == []
    assert ai_terms("Google Gemini rolls out to Workspace", "Google") == ["gemini"]
    assert ai_terms("Claude 4 tops coding benchmark", "Anthropic") == ["claude"]
    assert ai_terms("Copilot gets new AI agents", "X") == ["copilot", "ai agents"]


def test_startup_results_must_name_company():
    cat = {"label": "Multiverse Computing", "self": True, "site": "multiversecomputing.com"}
    assert cp._relevant({"title": "Multiverse Computing vs Zapata", "url": "https://a.com"}, cat)
    assert not cp._relevant({"title": "Multiverse vs Coursera", "url": "https://b.com",
                             "snippet": "Apprenticeship edtech"}, cat)
    assert cp._relevant({"title": "Rivals", "url": "https://multiversecomputing.com/x"}, cat)


def test_minor_categories_dropped_and_shared_name_pinned(monkeypatch):
    ents = {"QN": _ent("QN", "Netflix, Inc.", P1056=[_claim("QS"), _claim("QM")],
                       P2397=[_claim("UCn", kind="str")]),
            "QS": _ent("QS", "Netflix", "Netflix"), "QM": _ent("QM", "Millarworld", "Millarworld"),
            "QE": _ent("QE", "Gemini Intergalactic EU",
                       P856=[_claim("https://www.gemini.com", kind="str")]),
            "QG": {**_ent("QG", "Gemini"), "descriptions": {"en": {"value": "constellation"}}}}
    monkeypatch.setattr(wd, "get_entities", lambda ids, e: {i: ents[i] for i in ids if i in ents})
    monkeypatch.setattr(wd, "pageviews", lambda t, e, today=None: {"Netflix": 1000}.get(t, 20))
    monkeypatch.setattr(wd, "search", lambda n, e, **k: {"Netflix": "QN", "Gemini": "QG"}[n])
    assert [c["label"] for c in wd.resolve("Netflix")["categories"]] == ["Netflix"]  # 2% cut
    monkeypatch.setattr(wd, "by_website", lambda u, e: "QE")
    r = wd.resolve("Gemini", url="https://www.gemini.com")
    assert r["qid"] == "QE" and r["search_name"] == "Gemini"
    assert r["ambiguous_with"] == "Gemini (constellation)"
    assert cp._queries({"label": "Gemini", "self": True, "context": "crypto"})[0] == '"Gemini" crypto vs'
