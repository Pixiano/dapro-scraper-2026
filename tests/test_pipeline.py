"""Offline tests for the collect stage: fake collectors, no browser, no network."""

import asyncio
import threading
import time

import pytest

from backend import jobs, pipeline
from backend.collectors import base as cbase
from backend.config import settings


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "app.db")
    jobs.init_db()


def test_collectors_run_concurrently_capped_ordered_and_isolated(monkeypatch):
    lock, active, peak = threading.Lock(), [0], [0]

    def fake(url, job_dir):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with lock:
            active[0] -= 1
        if "bad" in url:
            raise RuntimeError("chromium crashed")
        return cbase.SourceArtifact(url=url, platform=cbase.WEBSITE, ok=True)

    monkeypatch.setattr(cbase, "_REGISTRY", {cbase.WEBSITE: fake})
    monkeypatch.setattr(settings, "collect_concurrency", 2)
    monkeypatch.setattr(settings, "enable_news", False)
    links = ["https://a.com", "https://bad.com", "https://c.com", "https://d.com"]
    jid = jobs.create({"links": links})

    arts = asyncio.run(pipeline._collect(jid, {"links": links}))

    assert peak[0] == 2                                   # at once, but capped
    assert [a["url"] for a in arts] == links              # order kept
    assert [a["ok"] for a in arts] == [True, False, True, True]
    assert "chromium crashed" in arts[1]["errors"][0]     # only that source failed
    assert jobs.get(jid)["status"] == jobs.COLLECTING     # progress update kept
