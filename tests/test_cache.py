import asyncio

import pytest

from backend import cache
from backend.config import settings
from backend.youtube.client import YouTubeError


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    cache.init_db()


def test_roundtrip():
    cache.set("k1", "snapshot", {"a": 1})
    payload, fetched_at = cache.get("k1")
    assert payload == {"a": 1}
    assert fetched_at  # iso string


def test_miss():
    assert cache.get("nope") is None


def test_expiry(monkeypatch):
    cache.set("k1", "snapshot", {"a": 1})
    monkeypatch.setattr(settings, "ttl_snapshot_s", 0)
    assert cache.get("k1") is None


def test_unknown_kind_rejected():
    with pytest.raises(KeyError):
        cache.set("k1", "bogus", {})


def test_cached_call_hits_cache_second_time():
    calls = []

    async def produce():
        calls.append(1)
        return {"v": 42}

    async def run():
        d1, c1, _ = await cache.cached_call("key", "snapshot", produce)
        d2, c2, _ = await cache.cached_call("key", "snapshot", produce)
        d3, c3, _ = await cache.cached_call("key", "snapshot", produce, force=True)
        return d1, c1, d2, c2, d3, c3

    d1, c1, d2, c2, d3, c3 = asyncio.run(run())
    assert d1 == d2 == d3 == {"v": 42}
    assert (c1, c2, c3) == (False, True, False)
    assert len(calls) == 2  # second call served from cache; force bypassed it


def test_negative_caching_reraises_without_refetch():
    calls = []

    async def produce():
        calls.append(1)
        raise YouTubeError(404, "channelNotFound", "no such channel", negative=True)

    async def run_once():
        await cache.cached_call("neg", "snapshot", produce)

    with pytest.raises(YouTubeError) as e1:
        asyncio.run(run_once())
    with pytest.raises(YouTubeError) as e2:
        asyncio.run(run_once())
    assert len(calls) == 1  # second raise came from cache
    assert e2.value.reason == "channelNotFound"
    assert e2.value.status == 404


def test_non_negative_errors_not_cached():
    calls = []

    async def produce():
        calls.append(1)
        raise YouTubeError(502, "upstreamUnreachable", "boom", negative=False)

    async def run_once():
        await cache.cached_call("err", "snapshot", produce)

    for _ in range(2):
        with pytest.raises(YouTubeError):
            asyncio.run(run_once())
    assert len(calls) == 2  # transient errors are retried, not cached
