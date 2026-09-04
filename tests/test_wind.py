"""Weather cache: placeholder fill on failure, cooldown gating, healing,
and per-race health. The HTTP layer is replaced; nothing touches the net."""
import time

import pytest

import vn.wind as wind
from vn.wind import get_current, get_wind, heal_fallback, wind_health


@pytest.fixture(autouse=True)
def fresh_cooldowns(monkeypatch):
    monkeypatch.setattr(wind, "_retry", {})
    monkeypatch.setattr(wind, "_failed", {})


def down(url, **kw):
    raise OSError("api down")


def wind_series(twd=270.0, tws=12.0, hours=range(-24, 48)):
    h0 = int(time.time()) // 3600 * 3600
    ts = [h0 + h * 3600 for h in hours]
    return {"hourly": {"time": ts, "wind_speed_10m": [tws] * len(ts),
                       "wind_direction_10m": [twd] * len(ts)}}


def current_series(kmh=3.704, deg=90.0, hours=range(-24, 48)):
    h0 = int(time.time()) // 3600 * 3600
    ts = [h0 + h * 3600 for h in hours]
    return {"hourly": {"time": ts, "ocean_current_velocity": [kmh] * len(ts),
                       "ocean_current_direction": [deg] * len(ts)}}


def count(db, table, source):
    return db.execute(f"SELECT COUNT(*) c FROM {table} WHERE source=?",
                      (source,)).fetchone()["c"]


def test_failed_fetch_fills_a_short_window_only(db, monkeypatch):
    calls = []
    monkeypatch.setattr(wind, "_http_json", lambda url, **kw: calls.append(url) or down(url))
    now = int(time.time())
    twd, tws, src = get_wind(db, 41.0, -71.0, now)
    assert src == "synthetic"
    assert count(db, "wind_cache", "synthetic") == 36          # 12 h back, 24 h on
    assert len(calls) == 1
    assert wind_health(db, now)["degraded"] is True


def test_a_failed_cell_is_not_refetched_every_step(db, monkeypatch):
    calls = []
    monkeypatch.setattr(wind, "_http_json", lambda url, **kw: calls.append(url) or down(url))
    now = int(time.time())
    get_wind(db, 41.0, -71.0, now)
    get_wind(db, 41.0, -71.0, now + 600)
    get_wind(db, 41.0, -71.0, now + 40 * 3600)     # outside the filled window
    assert len(calls) == 1
    assert get_wind(db, 41.0, -71.0, now + 40 * 3600)[2] == "synthetic"


def test_successful_fetch_after_failure_replaces_placeholders(db, monkeypatch):
    monkeypatch.setattr(wind, "_http_json", down)
    now = int(time.time())
    get_wind(db, 41.0, -71.0, now)
    monkeypatch.setattr(wind, "_http_json", lambda url, **kw: wind_series())
    monkeypatch.setattr(wind, "_retry", {})                   # cooldown elapsed
    twd, tws, src = get_wind(db, 41.0, -71.0, now)
    assert (twd, tws, src) == (270.0, 12.0, "open-meteo")
    assert count(db, "wind_cache", "synthetic") == 0
    assert wind_health(db, now)["degraded"] is False


def test_heal_refetches_placeholder_cells_without_a_boat_crossing(db, monkeypatch):
    monkeypatch.setattr(wind, "_http_json", down)
    now = int(time.time())
    for lon in (-71.0, -72.0, -73.0):
        get_wind(db, 41.0, lon, now)
    assert wind_health(db, now)["synthetic_cells"] == 3
    monkeypatch.setattr(wind, "_http_json", lambda url, **kw: wind_series())
    assert heal_fallback(db, now) == 0            # every cell tried within the cooldown
    monkeypatch.setattr(wind, "_retry", {})
    assert heal_fallback(db, now, limit=2) == 2   # bounded work per tick
    assert wind_health(db, now)["synthetic_cells"] == 1
    monkeypatch.setattr(wind, "_retry", {})
    assert heal_fallback(db, now) == 1
    assert wind_health(db, now)["degraded"] is False


def test_heal_leaves_cells_alone_while_the_api_is_still_down(db, monkeypatch):
    monkeypatch.setattr(wind, "_http_json", down)
    now = int(time.time())
    get_wind(db, 41.0, -71.0, now)
    monkeypatch.setattr(wind, "_retry", {})
    assert heal_fallback(db, now) == 0
    assert wind_health(db, now)["degraded"] is True


def test_health_is_scoped_to_a_box(db):
    now = int(time.time())
    db.execute("INSERT INTO wind_cache(lat,lon,t,twd,tws,source) VALUES (41,-71,?,0,10,'synthetic')",
               (now // 3600 * 3600,))
    db.commit()
    assert wind_health(db, now)["degraded"] is True
    assert wind_health(db, now, (40, 42, -72, -70))["degraded"] is True
    assert wind_health(db, now, (-10, 10, 0, 20))["degraded"] is False


def test_health_ignores_placeholders_outside_the_live_window(db):
    now = int(time.time())
    db.execute("INSERT INTO wind_cache(lat,lon,t,twd,tws,source) VALUES (41,-71,?,0,10,'synthetic')",
               (now + 5 * 86400,))
    db.commit()
    assert wind_health(db, now)["degraded"] is False


def test_current_failure_fills_short_window_and_heals(db, monkeypatch):
    monkeypatch.setattr(wind, "_http_json", down)
    now = int(time.time())
    assert get_current(db, 41.0, -71.0, now) == (0.0, 0.0, "none")
    assert count(db, "current_cache", "none") == 36
    monkeypatch.setattr(wind, "_http_json", lambda url, **kw: current_series())
    monkeypatch.setattr(wind, "_retry", {})
    assert heal_fallback(db, now) == 1
    cdir, cspd, src = get_current(db, 41.0, -71.0, now)
    assert (cdir, src) == (90.0, "open-meteo")
    assert cspd == pytest.approx(2.0, rel=0.01)                 # 3.704 km/h
    assert count(db, "current_cache", "none") == 0
