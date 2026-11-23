"""Offline tests for the company report's releases and filings sources (mocked HTTP)."""

from datetime import date

from backend.company import collect as co

BOUNDS = co.quarter_bounds(date(2026, 11, 17))  # Q2, Q3, Q4 2026 (to date)


class R:
    def __init__(self, data, code=200):
        self.data, self.status_code = data, code

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_hugging_face_org_must_be_verified_and_named(monkeypatch):
    orgs = {"acme": {"name": "acme", "fullname": "Acme", "isVerified": True},
            "acmelabs": {"name": "acmelabs", "fullname": "Acme Labs", "isVerified": False},
            "fb": {"name": "fb", "fullname": "AI at Acme", "isVerified": True}}
    models = [{"id": "acme/big-1", "createdAt": "2026-10-02T00:00:00.000Z", "likes": 9},
              {"id": "acme/old", "createdAt": "2025-01-01T00:00:00.000Z"}]
    seen: list[str] = []

    def http(url, **p):
        seen.append(url)
        if url.endswith("/overview"):
            o = orgs.get(url.split("/")[-2])
            return R(o or {}, 200 if o else 404)
        assert p["author"] in ("acme", "fb", "mistralai")
        return R(models)
    monkeypatch.setattr(co, "_http", http)
    items, org = co.hugging_face("Acme", {}, BOUNDS, [])
    assert org == "acme" and [(i["title"], i["quarter"], i["likes"]) for i in items] == [
        ("acme/big-1", "Q4 2026 (to date)", 9)]
    # GitHub account first; "AI at Acme" names the company
    assert co.hugging_face("Acme", {"github": "https://github.com/fb"}, BOUNDS, [])[1] == "fb"
    assert co.hugging_face("Acme Labs", {}, BOUNDS, [])[1] is None  # unverified lookalike
    seen.clear()  # Wikidata's id is trusted as is: no lookup
    co.hugging_face("Mistral", {"huggingface": "https://huggingface.co/mistralai"}, BOUNDS, [])
    assert not any(u.endswith("/overview") for u in seen)
    errors: list[str] = []
    monkeypatch.setattr(co, "_http", lambda url, **p: (_ for _ in ()).throw(OSError("offline")))
    assert co.hugging_face("Acme", {}, BOUNDS, errors) == ([], None) and len(errors) == 1


def test_github_repos_skip_forks_page_and_send_token(monkeypatch):
    def repo(n, day, fork=False):
        return {"full_name": f"acme/r{n}", "html_url": f"https://github.com/acme/r{n}",
                "created_at": f"{day}T00:00:00Z", "fork": fork, "stargazers_count": n}
    pages = {1: [repo(i, "2026-10-05", fork=i % 10 == 0) for i in range(100)],
             2: [repo(100, "2026-08-01"), repo(101, "2026-01-01")]}
    calls: list[dict] = []

    def get(url, headers, params, **k):
        calls.append(headers)
        assert url == "https://api.github.com/users/acme/repos" and params["sort"] == "created"
        return R(pages.get(params["page"], []))
    monkeypatch.setattr(co.httpx, "get", get)
    monkeypatch.setattr(co.settings, "github_token", "tok")
    items = co.github_repos({"github": "https://github.com/acme"}, BOUNDS, [])
    assert len(items) == 91 and len(calls) == 2  # 10 forks + one too old; stops past the start
    assert calls[0]["Authorization"] == "Bearer tok"
    assert co.github_repos({}, BOUNDS, []) == []  # no account: skipped silently
    errors: list[str] = []
    monkeypatch.setattr(co.httpx, "get", lambda *a, **k: R({}, 403))
    assert co.github_repos({"github": "https://github.com/acme"}, BOUNDS, errors) == []
    assert errors == ["github: RuntimeError: HTTP 403"]


def test_sec_filings_keep_current_reports_with_item_names(monkeypatch):
    rec = {"form": ["8-K", "4", "6-K", "8-K"],
           "filingDate": ["2026-11-05", "2026-11-04", "2026-08-20", "2025-01-01"],
           "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3", "0001-25-4"],
           "primaryDocument": ["a.htm", "b.xml", "c.htm", "d.htm"],
           "items": ["5.02,9.01", "", "", "2.02"]}
    got: dict = {}

    def get(url, headers, **k):
        got.update(url=url, ua=headers["User-Agent"])
        return R({"filings": {"recent": rec}})
    monkeypatch.setattr(co.httpx, "get", get)
    sec = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000789019"
    items = co.sec_filings({"sec": sec}, BOUNDS, [])
    assert got["url"] == "https://data.sec.gov/submissions/CIK0000789019.json"
    assert got["ua"] == co.UA  # SEC wants a contact in the User-Agent
    assert [(i["title"], i["url"]) for i in items] == [
        ("Form 8-K filed 5 Nov 2026: officer or director change",
         "https://www.sec.gov/Archives/edgar/data/789019/0001261/a.htm"),
        ("Form 6-K filed 20 Aug 2026: foreign issuer report",
         "https://www.sec.gov/Archives/edgar/data/789019/0001263/c.htm")]
    assert co.sec_filings({}, BOUNDS, []) == []  # not US-listed: skipped silently


def test_releases_are_counted_apart_from_headlines(tmp_path, monkeypatch):
    from backend.company import linkcheck, report
    monkeypatch.setattr(linkcheck, "check_all", lambda urls: {
        u: {"status": "live", "code": 200, "checked_at": "t"} for u in urls})
    q = [{"label": lb, "start": s.isoformat(), "end": e.isoformat()} for lb, s, e in BOUNDS]
    last, cur = q[1]["label"], q[2]["label"]

    def it(source, title, quarter, **x):
        return {"source": source, "title": title, "url": f"https://x/{title}",
                "published": "2026-10-02" if quarter == cur else "2026-08-02",
                "quarter": quarter, **x}
    acme = {"label": "Acme", "searched_as": "Acme", "errors": [],
            "links": {"huggingface": "https://huggingface.co/acme",
                      "github": "https://github.com/acme"},
            "items": [it("google_news", "Acme ships AI agents", cur),
                      it("google_news", "Acme lunch menu", cur),
                      it("huggingface", "acme/model-a", last, likes=5),
                      it("huggingface", "acme/model-b", cur, likes=50),
                      it("github", "acme/llm-tools", cur, stars=3),
                      it("github", "acme/website", cur, stars=9, description="our site")]}
    raw = {"company": {"label": "Acme", "links": acme["links"]},
           "competitors": {"mode": "user", "overall": [{"label": "Rival", "evidence": []}]},
           "collected": {"quarters": q, "company": acme, "competitors": [
               {"label": "Rival", "searched_as": "Rival", "links": {}, "errors": [], "items": []}]}}
    report.build(raw, tmp_path)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert f"{cur} 1/2" in md  # one AI headline of two: models and repos are not headlines
    assert "| Acme | 1/1 | 0/2 | - |" in md and "| Rival | - | - | - |" in md
    assert ("Hugging Face models 1/1: acme/model-b [" in md
            and "new public GitHub repos 0/2 (0/1 AI by keyword): acme/website [" in md)
