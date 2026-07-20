import asyncio
from pathlib import Path

import pytest

from backend import jobs, pipeline
from backend.collectors.base import FACEBOOK, INSTAGRAM, WEBSITE, YOUTUBE, classify_url
from backend.config import settings


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "app.db")
    jobs.init_db()


@pytest.mark.parametrize("url,platform", [
    ("https://www.youtube.com/@mkbhd", YOUTUBE),
    ("https://youtu.be/abc", YOUTUBE),
    ("https://instagram.com/nasa", INSTAGRAM),
    ("https://www.facebook.com/nasa", FACEBOOK),
    ("https://mbasic.facebook.com/nasa", FACEBOOK),
    ("https://example.com/about", WEBSITE),
    ("acme.io", WEBSITE),
])
def test_classify(url, platform):
    assert classify_url(url) == platform


def test_job_runs_through_all_states_and_persists(monkeypatch):
    # Keep this test offline: force the stub for every platform, stub the
    # synthesis model call (otherwise this loads gpt-oss and takes ~16s), and
    # disable the entity-name news feed (it would fetch Google News for real).
    from backend.collectors import base as cbase

    monkeypatch.setattr(cbase, "_REGISTRY", {})
    monkeypatch.setattr(settings, "enable_news", False)
    monkeypatch.setattr(
        pipeline.synthesis, "build_dossier",
        lambda name, arts: f"# Research Dossier — {name}\n\nStub synthesis.\n")

    jid = jobs.create({"links": ["https://example.com", "https://youtube.com/@x"],
                       "entity_name": "Test Entity"})
    assert jobs.get(jid)["status"] == jobs.QUEUED

    asyncio.run(pipeline.run_job(jid))

    job = jobs.get(jid)
    assert job["status"] == jobs.DONE
    assert job["error"] is None
    assert len(job["result"]["artifacts"]) == 2
    assert job["dossier_path"] and Path(job["dossier_path"]).exists()
    text = Path(job["dossier_path"]).read_text(encoding="utf-8")
    assert "Test Entity" in text
    # stub collectors marked each source pending, not ok
    assert all(a["ok"] is False for a in job["result"]["artifacts"])


def _offline_job(monkeypatch):
    """Stub collectors + synthesis so a job runs with no network / no model."""
    from backend.collectors import base as cbase

    monkeypatch.setattr(cbase, "_REGISTRY", {})
    monkeypatch.setattr(pipeline.synthesis, "build_dossier",
                        lambda name, arts: "# Dossier\n")


def test_news_source_appended_when_enabled(monkeypatch):
    _offline_job(monkeypatch)
    monkeypatch.setattr(settings, "enable_news", True)
    called = {}

    def fake_news(entity, jdir):
        called["entity"] = entity
        from backend.collectors.base import NEWS, SourceArtifact
        return SourceArtifact(url="news://x", platform=NEWS, ok=True,
                              text_blocks=[{"label": "recent news", "text": "- headline"}])

    monkeypatch.setattr(pipeline.news, "collect_for_entity", fake_news)

    jid = jobs.create({"links": ["https://example.com"], "entity_name": "Acme"})
    asyncio.run(pipeline.run_job(jid))

    arts = jobs.get(jid)["result"]["artifacts"]
    assert called["entity"] == "Acme"           # entity name drives the feed
    assert len(arts) == 2                        # 1 link + 1 auto news source
    assert arts[-1]["platform"] == "news"


@pytest.mark.parametrize("enabled,entity,expected", [
    (False, "Acme", 1),      # flag off → no news source
    (True, "", 1),           # no entity name → nothing to search for
    (True, None, 1),
])
def test_news_source_skipped(monkeypatch, enabled, entity, expected):
    _offline_job(monkeypatch)
    monkeypatch.setattr(settings, "enable_news", enabled)
    monkeypatch.setattr(pipeline.news, "collect_for_entity",
                        lambda *a: pytest.fail("news must not be fetched"))

    jid = jobs.create({"links": ["https://example.com"], "entity_name": entity})
    asyncio.run(pipeline.run_job(jid))
    assert len(jobs.get(jid)["result"]["artifacts"]) == expected


def test_job_error_is_captured(monkeypatch):
    jid = jobs.create({"links": ["https://example.com"], "entity_name": "X"})

    async def boom(job_id, links):
        raise RuntimeError("collect exploded")

    monkeypatch.setattr(pipeline, "_collect", boom)
    asyncio.run(pipeline.run_job(jid))

    job = jobs.get(jid)
    assert job["status"] == jobs.ERROR
    assert "collect exploded" in job["error"]


def test_recent_lists_newest_first():
    a = jobs.create({"links": ["https://a.com"]})
    b = jobs.create({"links": ["https://b.com"]})
    ids = [j["id"] for j in jobs.recent()]
    assert ids.index(b) < ids.index(a)


def test_ids_are_sequential_from_ABC0001():
    assert jobs.create({"links": ["x"]}) == "ABC0001"
    assert jobs.create({"links": ["y"]}) == "ABC0002"
    assert jobs.create({"links": ["z"]}) == "ABC0003"


def test_next_id_ignores_legacy_uuid_rows():
    conn = jobs._connect()
    conn.execute("INSERT INTO jobs (id, status, inputs, created_at, updated_at) "
                 "VALUES ('deadbeefcafe0001','done','{}',1,1)")
    conn.commit(); conn.close()
    assert jobs.create({"links": ["x"]}) == "ABC0001"  # uuid row doesn't shift the count


@pytest.mark.parametrize("n,expected", [
    (1, "ABC0001"), (2, "ABC0002"), (9999, "ABC9999"),
    (10000, "ABD0001"), (10001, "ABD0002"),
    (9999 * 24, "ABZ9999"), (9999 * 24 + 1, "ACA0001"),   # ABZ -> ACA rollover
])
def test_encode_id(n, expected):
    assert jobs._encode_id(n) == expected


def test_encode_decode_roundtrip():
    for n in (1, 2, 9999, 10000, 10001, 9999 * 26 * 26, 175_000_000):
        assert jobs._decode_id(jobs._encode_id(n)) == n


def test_zzz_boundary_widens_to_four_letters():
    zzz_last = 9999 * (26 ** 3 - 1 - 28 + 1)   # last ordinal with prefix "ZZZ"
    assert jobs._encode_id(zzz_last) == "ZZZ9999"
    assert jobs._encode_id(zzz_last + 1) == "BAAA0001"
    assert jobs._decode_id("BAAA0001") == zzz_last + 1


def test_decode_rejects_non_ids():
    assert jobs._decode_id("deadbeefcafe0001") is None
    assert jobs._decode_id("ABC12345") is None
    assert jobs._decode_id("AB0001") is None
    assert jobs._decode_id("ABC0000") is None
