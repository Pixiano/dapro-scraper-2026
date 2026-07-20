import pytest

from backend import quota
from backend.config import settings
from backend.quota import QuotaExhausted


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    quota.init_db()


def test_costs():
    assert quota.cost_of("channels") == 1
    assert quota.cost_of("playlistItems") == 1
    assert quota.cost_of("captions") == 50
    assert quota.cost_of("search") == 100


def test_ledger_accumulates():
    assert quota.used_today() == 0
    quota.charge("channels")
    quota.charge("videos")
    quota.charge("captions")
    assert quota.used_today() == 52
    st = quota.status()
    assert st["used"] == 52
    assert st["remaining"] == settings.quota_soft_stop - 52


def test_soft_stop_blocks(monkeypatch):
    monkeypatch.setattr(settings, "quota_soft_stop", 100)
    quota.charge("search")  # 100 — reaching the cap exactly is allowed
    with pytest.raises(QuotaExhausted):
        quota.charge("videos")  # 100 + 1 > 100
    assert quota.used_today() == 100  # blocked call charged nothing


def test_snapshot_cost_prediction():
    # channels + playlistItems + videos + playlists + channelSections = 5 units
    for r in ("channels", "playlistItems", "videos", "playlists", "channelSections"):
        quota.charge(r)
    assert quota.used_today() == 5
