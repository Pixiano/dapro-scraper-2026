"""Offline tests for the company mechanism (no network)."""

from datetime import date

from backend.company import wikidata as wd


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
    monkeypatch.setattr(wd, "search", lambda n, e, prefer_company=True: qids.get(n))
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
    monkeypatch.setattr(wd, "search", lambda n, e, prefer_company=True: {"Bing": "QB2", "Google": "QG"}[n])
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
