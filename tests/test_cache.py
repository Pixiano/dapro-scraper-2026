import pytest

from backend import cache
from backend.config import settings


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
