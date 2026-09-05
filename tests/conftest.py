"""Shared fixtures.

Every test gets a fresh SQLite file and deterministic weather: wind, current
and depth lookups in the engine are replaced so nothing touches the network
and every run sails the same sea.
"""
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.pop("VN_ENABLE_TICKER", None)       # never start the ticker under test
os.environ.setdefault("VN_DB", str(ROOT / "tests" / ".unused.sqlite"))

import vn.db as vndb                            # noqa: E402
import vn.sim as sim                            # noqa: E402

POLAR_40FT = (ROOT / "data" / "polar_40ft.pol").read_text()
POLAR_CLASS40 = (ROOT / "data" / "polar_class40.pol").read_text()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh database file for this test, on this thread."""
    monkeypatch.setattr(vndb, "DB_PATH", str(tmp_path / "vn.sqlite"))
    old = getattr(vndb._local, "conn", None)
    if old is not None:
        old.close()
    vndb._local.conn = None
    conn = vndb.get_db()
    yield conn
    conn.close()
    vndb._local.conn = None


class Weather:
    """Controls what the engine sees. Defaults: 15 kn from due north, no
    current, deep water everywhere."""

    def __init__(self):
        self.wind = lambda lat, lon, t: (0.0, 15.0)
        self.current = lambda lat, lon, t: (0.0, 0.0)
        self.depth_ft = lambda lat, lon: 9999.0

    def steady(self, twd, tws):
        self.wind = lambda lat, lon, t: (float(twd), float(tws))


@pytest.fixture
def weather(monkeypatch):
    w = Weather()
    monkeypatch.setattr(sim, "get_wind", lambda db, lat, lon, t: (*w.wind(lat, lon, t), "test"))
    monkeypatch.setattr(sim, "get_current", lambda db, lat, lon, t: (*w.current(lat, lon, t), "test"))
    monkeypatch.setattr(sim, "get_depth_ft", lambda db, lat, lon: w.depth_ft(lat, lon))
    return w


# ---- row factories ----------------------------------------------------------

def make_race(db, marks, *, start_time=0, step_minutes=10, mark_radius_nm=2.0,
              perf_factor=0.9, penalty_s=120, polar_text=POLAR_40FT,
              currents=False, depth_ft=15, zones=None, name="Test Race"):
    """marks: [(name, lat, lon)] or [(name, lat, lon, side)]."""
    import json
    cur = db.execute(
        "INSERT INTO races(name,start_time,perf_factor,step_minutes,mark_radius_nm,"
        "polar_name,polar_text,admin_key,created_at,maneuver_penalty_s,"
        "currents_enabled,grounding_depth_ft,zones_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, start_time, perf_factor, step_minutes, mark_radius_nm, "test polar",
         polar_text, "", int(time.time()), penalty_s, 1 if currents else 0,
         depth_ft, json.dumps(zones or [])))
    race_id = cur.lastrowid
    for i, m in enumerate(marks):
        side = m[3] if len(m) > 3 else None
        db.execute("INSERT INTO marks(race_id,seq,name,lat,lon,side) VALUES (?,?,?,?,?,?)",
                   (race_id, i, m[0], m[1], m[2], side))
    db.commit()
    return race_id


def make_boat(db, race_id, *, name="Tester", owner_id=None, started_at=None,
              lat=None, lon=None):
    """A virtual boat; started_at places it on the start mark at that time."""
    if started_at is not None and lat is None:
        m0 = db.execute("SELECT lat,lon FROM marks WHERE race_id=? AND seq=0",
                        (race_id,)).fetchone()
        lat, lon = m0["lat"], m0["lon"]
    cur = db.execute(
        "INSERT INTO boats(race_id,name,pin_hash,created_at,owner_id,sim_time,lat,lon,"
        "next_mark) VALUES (?,?,'',?,?,?,?,?,1)",
        (race_id, name, int(time.time()), owner_id, started_at, lat, lon))
    db.commit()
    return cur.lastrowid


def set_route(db, boat_id, wps):
    db.execute("DELETE FROM route_wps WHERE boat_id=? AND passed=0", (boat_id,))
    row = db.execute("SELECT COALESCE(MAX(seq),-1) m FROM route_wps WHERE boat_id=?",
                     (boat_id,)).fetchone()
    base = row["m"] + 1
    db.executemany("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,?,?,?)",
                   [(boat_id, base + i, la, lo) for i, (la, lo) in enumerate(wps)])
    db.commit()


def boat_row(db, boat_id):
    return db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()


def track_rows(db, boat_id):
    return [tuple(r) for r in db.execute(
        "SELECT t,lat,lon,twd,tws,bsp,hdg,src FROM track WHERE boat_id=? ORDER BY t",
        (boat_id,))]


# ---- Flask client ----------------------------------------------------------

@pytest.fixture
def client(db, weather):
    """A Flask test client on the fresh database, weather mocked."""
    import app as appmod
    appmod.app.config["TESTING"] = True
    appmod._buckets.clear()                     # rate-limit windows start fresh
    appmod._state_cache.clear()                 # and no state from another test's database
    return appmod.app.test_client()


@pytest.fixture
def app_module(client):
    import app as appmod
    return appmod


def new_client(username, password="secret1", display=None):
    """A separate cookie jar signed in as a fresh account."""
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/auth/register", json={"username": username, "password": password,
                                            "display_name": display or username})
    assert r.status_code == 200, r.get_json()
    return c
