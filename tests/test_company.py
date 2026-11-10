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


def test_resolve_not_found(monkeypatch):
    monkeypatch.setattr(wd, "search", lambda n, e: None)
    r = wd.resolve("Nope Corp")
    assert r["qid"] is None and r["errors"]
