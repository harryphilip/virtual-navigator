"""The fleet-state endpoint: SQL-thinned tracks, deltas, and the per-tick cache."""
import time

from vn.sim import catch_up_race
from tests.conftest import make_boat, make_race, new_client, set_route

H = 3600
START, FINISH = ("Start", 0.0, 0.0), ("Finish", -5.0, 0.0)


def seed_track(db, boat, n, t0=0, step=600):
    db.executemany(
        "INSERT INTO track(boat_id,t,lat,lon,twd,tws,bsp,hdg,src) VALUES (?,?,?,?,0,10,7.5,180,'test')",
        [(boat, t0 + i * step, -i * 0.001, 0.0) for i in range(n)])
    db.execute("UPDATE boats SET sim_time=?, lat=?, lon=? WHERE id=?",
               (t0 + (n - 1) * step, -(n - 1) * 0.001, 0.0, boat))
    db.commit()


def test_full_state_thins_long_tracks_in_sql_and_keeps_the_ends(client, db, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_ticker_started", True)     # serve stored state, no catch-up
    race = make_race(db, [START, FINISH])
    long, short = make_boat(db, race, name="Long", started_at=0), make_boat(db, race, name="Short", started_at=0)
    seed_track(db, long, 1000)
    seed_track(db, short, 50)
    st = client.get(f"/api/races/{race}/state").get_json()
    by = {e["name"]: e for e in st["entries"]}
    assert 200 <= len(by["Long"]["track"]) <= 401           # stride rounds up: 1000 -> 334
    assert by["Long"]["track"][0] == [0.0, 0.0, 0] and by["Long"]["track"][-1][2] == 999 * 600
    assert by["Long"]["track_n"] == 1000 and by["Long"]["last_t"] == 999 * 600
    assert by["Long"]["sog"] == 7.5
    assert len(by["Short"]["track"]) == 50
    assert st["delta"] is False


def test_real_tracks_are_the_newest_three_hundred(client, db, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_ticker_started", True)
    race = make_race(db, [START, FINISH])
    rb = db.execute("INSERT INTO real_boats(race_id,name,klass,last_t,last_lat,last_lon,next_mark) "
                    "VALUES (?,?,?,?,?,?,1)", (race, "Real One", "IMOCA", 1000, 0.0, 0.0)).lastrowid
    db.executemany("INSERT INTO real_track(rb_id,t,lat,lon) VALUES (?,?,?,?)",
                   [(rb, i * 60, -i * 0.001, 0.0) for i in range(500)])
    db.commit()
    e = client.get(f"/api/races/{race}/state").get_json()["entries"][0]
    assert e["type"] == "real" and len(e["track"]) == 300
    assert e["track"][0][2] == 200 * 60 and e["track"][-1][2] == 499 * 60
    assert e["track_n"] == 500


def test_delta_returns_only_newer_fixes(client, db, weather):
    race = make_race(db, [START, FINISH])
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(-5.0, 0.0)])
    catch_up_race(db, race, now=H)
    full = client.get(f"/api/races/{race}/state").get_json()   # no ticker: this also catches up to now
    e = full["entries"][0]
    n0, last = e["track_n"], e["last_t"]
    assert n0 >= 6 and e["track"][-1][2] == last
    delta = client.get(f"/api/races/{race}/state?since={last}").get_json()
    d = delta["entries"][0]
    assert delta["delta"] is True and delta["since"] == last
    assert all(p[2] > last for p in d["track"])
    assert d["track_n"] == n0 + len(d["track"])
    assert d["lat"] == e["lat"] or len(d["track"]) > 0


def test_state_is_cached_per_tick_and_dropped_on_writes(client, db, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_ticker_started", True)
    appmod._state_cache.clear()
    calls = []
    real_build = appmod._build_state
    monkeypatch.setattr(appmod, "_build_state", lambda *a, **k: (calls.append(1), real_build(*a, **k))[1])
    admin = new_client("admin")
    from tests.test_api import race_body
    race = admin.post("/api/races", json=race_body()).get_json()["id"]
    client.get(f"/api/races/{race}/state")
    client.get(f"/api/races/{race}/state")
    assert len(calls) == 1                                      # served from the cache
    client.get(f"/api/races/{race}/state?since=0")
    assert len(calls) == 2                                      # deltas are never cached
    boat = admin.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    st = client.get(f"/api/races/{race}/state").get_json()
    assert len(calls) == 3 and st["entries"][0]["name"] == "Magpie"   # a new boat shows at once
    admin.post(f"/api/boats/{boat}/route", json={"waypoints": [[-5.0, 0.0]]})
    st = client.get(f"/api/races/{race}/state").get_json()
    assert len(calls) == 4 and st["entries"][0]["has_route"] is True
    # the cache expires after a tick even without a write
    appmod._state_cache[race] = (time.time() - appmod.STATE_CACHE_SECONDS - 1, appmod._state_cache[race][1])
    client.get(f"/api/races/{race}/state")
    assert len(calls) == 5


def test_the_ticker_drops_the_cache(client, db, weather, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_ticker_started", True)
    race = make_race(db, [START, FINISH], start_time=int(time.time()) - 3600)
    client.get(f"/api/races/{race}/state")
    assert race in appmod._state_cache
    appmod._tick()
    assert race not in appmod._state_cache
