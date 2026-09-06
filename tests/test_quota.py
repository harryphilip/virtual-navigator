"""The Open-Meteo call gauge on /healthz."""
import logging

from vn import quota


def setup_function():
    quota.reset()


def test_counts_by_kind_over_a_day_and_an_hour():
    t = 1_800_000_000
    quota.note_calls("wind", now=t - 5 * 3600)
    quota.note_calls("current", now=t - 5 * 3600)
    quota.note_calls("forecast", 40, now=t - 600)
    quota.note_calls("wind", now=t - 60)
    s = quota.summary(now=t)
    assert s["calls_24h"] == 43 and s["calls_1h"] == 41
    assert s["by_kind_24h"] == {"wind": 2, "current": 1, "forecast": 40}
    assert s["limit_day"] == 10_000 and s["limit_hour"] == 5_000


def test_calls_older_than_a_day_fall_off():
    t = 1_800_000_000
    quota.note_calls("wind", 500, now=t - 90_000)
    quota.note_calls("wind", now=t)
    assert quota.summary(now=t)["calls_24h"] == 1


def test_warns_once_an_hour_near_the_limit(caplog):
    t = 1_800_000_000
    with caplog.at_level(logging.WARNING, logger="vn.quota"):
        quota.note_calls("wind", 8_000, now=t)
        quota.note_calls("wind", 1, now=t + 60)          # same hour: quiet
        quota.note_calls("wind", 1, now=t + 3700)        # next hour: again
    assert sum("Open-Meteo" in r.message for r in caplog.records) == 2


def test_healthz_reports_the_gauge(client):
    quota.note_calls("forecast", 12)
    body = client.get("/healthz").get_json()
    assert body["open_meteo"]["calls_24h"] == 12
    assert body["open_meteo"]["by_kind_24h"] == {"forecast": 12}
