import time


def test_healthz_without_a_ticker_reports_the_database_only(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["ticker"] is False
    assert body["version"]                      # the build's commit, or "dev"
    assert body["weather"]["degraded"] is False


def test_healthz_fails_when_the_ticker_stops_ticking(client, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_ticker_started", True)
    # never ticked at all
    monkeypatch.setattr(appmod, "_tick_started_at", None)
    monkeypatch.setattr(appmod, "_tick_done_at", None)
    assert client.get("/healthz").status_code == 503
    # ticked a moment ago
    now = time.time()
    monkeypatch.setattr(appmod, "_tick_started_at", now - 5)
    monkeypatch.setattr(appmod, "_tick_done_at", now - 4)
    assert client.get("/healthz").status_code == 200
    # last completed tick is ten minutes old and nothing is running
    monkeypatch.setattr(appmod, "_tick_started_at", now - 601)
    monkeypatch.setattr(appmod, "_tick_done_at", now - 600)
    assert client.get("/healthz").status_code == 503
    # a tick in progress is fine for a while, then counts as stuck
    monkeypatch.setattr(appmod, "_tick_started_at", now - 120)
    assert client.get("/healthz").status_code == 200
    monkeypatch.setattr(appmod, "_tick_started_at", now - 1000)
    assert client.get("/healthz").status_code == 503
