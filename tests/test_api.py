"""Route-level tests for backend/main.py and the company web runner.
Offline: temporary database, no model server, no browser, no network."""

import asyncio
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import jobs, main, worker
from backend.company import visual, web
from backend.config import settings
from backend.llm import server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "app.db")
    jobs.init_db()
    main.limiter.reset()


@pytest.fixture
def client():
    # no `with`: the lifespan (worker, model server) stays off unless a test asks
    return TestClient(main.app, base_url="http://localhost")


def test_file_route_serves_only_files_of_real_jobs(client, tmp_path):
    jid = jobs.create({"links": ["https://example.com"]})
    (jobs.job_dir(jid) / "shot.png").write_bytes(b"png")
    assert client.get(f"/api/jobs/{jid}/file?path=shot.png").content == b"png"

    # the review's exploit: ".." as the job id reached data/app.db
    assert client.get("/api/jobs/%2e%2e/file?path=app.db").status_code == 404
    assert client.get(f"/api/jobs/{jid}/file?path=../../app.db").status_code == 404
    assert client.get(f"/api/jobs/{jid}/file",
                      params={"path": str(tmp_path / "app.db")}).status_code == 404
    # an unknown id is a 404 and leaves no folder behind
    assert client.get("/api/jobs/nope/file?path=x.png").status_code == 404
    assert not (tmp_path / "jobs" / "nope").exists()


def test_only_public_http_links_are_accepted(client, monkeypatch):
    async def enqueue(job_id):
        pass
    monkeypatch.setattr(worker, "enqueue", enqueue)

    for bad in ("file:///C:/Windows/win.ini", "ftp://example.com/x",
                "http://127.0.0.1:8080/", "http://10.0.0.5/", "http://[::1]/"):
        r = client.post("/api/jobs", json={"links": [bad]})
        assert r.status_code == 400 and r.json()["error"]["reason"] == "badLink", bad
        r = client.post("/api/company/runs", json={"name": "Acme", "url": bad})
        assert r.status_code == 400, bad
    assert jobs.recent() == []

    # a name that resolves to loopback is refused too (no string matching)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    assert client.post("/api/jobs", json={"links": ["https://evil.example"]}).status_code == 400
    # public address, with or without a scheme: accepted
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert client.post("/api/jobs", json={"links": ["https://example.com", "acme.io"]}
                       ).status_code == 200


def test_company_screenshot_refuses_non_http(tmp_path, monkeypatch):
    import playwright.sync_api
    monkeypatch.setattr(playwright.sync_api, "sync_playwright",
                        lambda: pytest.fail("browser must not be launched"))
    assert visual.screenshot("file:///C:/Windows/win.ini", tmp_path / "x.png") \
        == "not an http(s) URL"


def test_foreign_host_header_is_refused(client):
    assert client.get("/api/jobs").status_code == 200
    assert client.get("/api/jobs", headers={"host": "127.0.0.1:8123"}).status_code == 200
    assert client.get("/api/jobs", headers={"host": "evil.example"}).status_code == 400


def test_startup_fails_jobs_cut_off_by_a_restart():
    stuck, queued, done = (jobs.create({"links": ["https://example.com"]}) for _ in range(3))
    jobs.update(stuck, status=jobs.SYNTHESIZING)
    jobs.update(done, status=jobs.DONE)
    with TestClient(main.app, base_url="http://localhost"):
        pass
    for jid in (stuck, queued):
        job = jobs.get(jid)
        assert (job["status"], job["error"]) == (jobs.ERROR, "interrupted by restart")
    assert jobs.get(done)["status"] == jobs.DONE and jobs.get(done)["error"] is None


def test_worker_waits_while_a_company_run_holds_the_model(monkeypatch):
    ran = []

    async def run_job(job_id):
        ran.append(job_id)
    monkeypatch.setattr(worker.pipeline, "run_job", run_job)

    async def go():
        worker.start()
        with server.run_lock:
            await worker.enqueue("J1")
            await asyncio.sleep(0.3)
            assert ran == []          # held back: the model is in use
        await asyncio.wait_for(worker._queue.join(), timeout=5)
        await worker.stop()

    asyncio.run(go())
    assert ran == ["J1"] and not server.run_lock.locked()


def test_company_web_run_holds_the_lock_and_stops_the_server(monkeypatch):
    seen = []

    def run(*a, **k):
        seen.append(("run", server.run_lock.locked()))
        raise RuntimeError("boom")
    monkeypatch.setattr(web, "run", run)
    monkeypatch.setattr(server, "stop", lambda: seen.append(("stop", server.run_lock.locked())))
    monkeypatch.setitem(web._runs, "r1", {"id": "r1"})

    web._work("r1", web.RunIn(name="Acme"))

    assert seen == [("run", True), ("stop", True)]   # stopped even though the run failed
    assert web._runs["r1"]["status"] == "error" and not server.run_lock.locked()


def test_recent_list_returns_only_what_the_page_reads(client):
    jid = jobs.create({"links": ["https://example.com"], "entity_name": "Acme"})
    jobs.update(jid, result={"artifacts": [{"url": "https://example.com", "text_blocks": []}]})
    (row,) = client.get("/api/jobs").json()["jobs"]
    assert set(row) == {"id", "status", "inputs", "created_at", "dossier_available"}
    # the full view is still there for one job
    assert len(client.get(f"/api/jobs/{jid}").json()["artifacts"]) == 1


def test_v1_youtube_routes_are_gone(client):
    for path in ("/api/quota", "/api/resolve?q=x", "/api/channel/UCx", "/api/instagram/nasa"):
        assert client.get(path).status_code == 404, path


def test_frontend_has_no_inline_event_handlers():
    # an inline onclick built from a path breaks (and injects) on a quote
    for name in ("app.js", "index.html", "company.html"):
        assert "onclick=" not in (ROOT / "frontend" / name).read_text(encoding="utf-8"), name
