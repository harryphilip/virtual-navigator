"""Virtual boats start when the real fleet does (vn/fleetgate.py)."""
import time

from tests.conftest import POLAR_40FT, make_race, new_client
from vn import fleetgate

MARKS = [("Start", 0.0, 0.0), ("Finish", -0.5, 0.0)]
H = 3600


def race_with_fleet(db, n_real, start_time=None, pct=None):
    start_time = int(time.time()) - 2 * H if start_time is None else start_time
    race_id = make_race(db, MARKS, start_time=start_time)
    if pct is not None:
        db.execute("UPDATE races SET fleet_start_pct=? WHERE id=?", (pct, race_id))
    for i in range(n_real):
        db.execute("INSERT INTO real_boats(race_id,name) VALUES (?,?)", (race_id, f"R{i}"))
    db.commit()
    return race_id


def race_row(db, race_id):
    return db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()


def fix(db, race_id, name, t, lat=0.0, lon=0.0):
    rb = db.execute("SELECT id FROM real_boats WHERE race_id=? AND name=?",
                    (race_id, name)).fetchone()
    db.execute("INSERT OR IGNORE INTO real_track(rb_id,t,lat,lon) VALUES (?,?,?,?)",
               (rb["id"], t, lat, lon))
    db.commit()


def api_race_with_fleet(db, n_real):
    """A race made through the API (so it carries the column default) plus a
    real fleet, and a navigator with a boat in it."""
    admin = new_client("admin")
    r = admin.post("/api/races", json={
        "name": "Gated", "start_time": int(time.time()) - 2 * H,
        "polar_text": POLAR_40FT, "currents_enabled": False,
        "marks": [{"name": "Start", "lat": 0.0, "lon": 0.0},
                  {"name": "Finish", "lat": -0.5, "lon": 0.0}]})
    race_id = r.get_json()["id"]
    for i in range(n_real):
        db.execute("INSERT INTO real_boats(race_id,name) VALUES (?,?)", (race_id, f"R{i}"))
    db.commit()
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race_id}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    return race_id, nav, boat


# ---- the gate itself ----------------------------------------------------

def test_no_real_fleet_means_no_gate(db):
    race_id = make_race(db, MARKS, start_time=1000)
    race = race_row(db, race_id)
    assert fleetgate.fleet_gate(db, race) is None
    assert fleetgate.virtual_start(db, race) == 1000


def test_gate_off_at_zero_percent(db):
    race = race_row(db, race_with_fleet(db, 20, start_time=1000, pct=0))
    assert fleetgate.fleet_gate(db, race) is None
    assert fleetgate.virtual_start(db, race) == 1000


def test_needed_is_five_percent_rounded_up_at_least_one(db):
    assert fleetgate.fleet_gate(db, race_row(db, race_with_fleet(db, 64)))["needed"] == 4
    assert fleetgate.fleet_gate(db, race_row(db, race_with_fleet(db, 10)))["needed"] == 1
    assert fleetgate.fleet_gate(db, race_row(db, race_with_fleet(db, 3, pct=50)))["needed"] == 2


def test_opens_when_the_needed_th_boat_is_first_seen_after_the_gun(db):
    race_id = race_with_fleet(db, 40, start_time=1000)          # needed: 2
    fix(db, race_id, "R0", 900)                                  # before the gun: not a start
    fix(db, race_id, "R0", 1300)
    g = fleetgate.fleet_gate(db, race_row(db, race_id))
    assert (g["started"], g["open_at"]) == (1, None)
    assert fleetgate.virtual_start(db, race_row(db, race_id)) is None
    fix(db, race_id, "R1", 1500)
    fix(db, race_id, "R1", 1560)
    g = fleetgate.fleet_gate(db, race_row(db, race_id))
    assert (g["started"], g["open_at"]) == (2, 1500)
    assert fleetgate.virtual_start(db, race_row(db, race_id)) == 1500


def test_open_gate_records_the_start_once_and_sends_waiting_boats_off(db):
    race_id = race_with_fleet(db, 40, start_time=1000)
    cur = db.execute("INSERT INTO boats(race_id,name,pin_hash,created_at) VALUES (?,?,'',1)",
                     (race_id, "Waiting"))
    waiting = cur.lastrowid
    db.execute("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,0,-0.5,0.0)", (waiting,))
    db.execute("INSERT INTO boats(race_id,name,pin_hash,created_at) VALUES (?,?,'',1)",
               (race_id, "No route"))
    db.commit()
    assert fleetgate.open_gate(db, race_row(db, race_id)) is None      # still waiting
    fix(db, race_id, "R0", 1200)
    fix(db, race_id, "R1", 1800)
    assert fleetgate.open_gate(db, race_row(db, race_id)) == 1800
    race = race_row(db, race_id)
    assert race["virtual_start"] == 1800
    b = db.execute("SELECT * FROM boats WHERE id=?", (waiting,)).fetchone()
    assert (b["sim_time"], b["lat"], b["lon"]) == (1800, 0.0, 0.0)
    assert db.execute("SELECT sim_time FROM boats WHERE name='No route'").fetchone()[0] is None
    log = db.execute("SELECT message FROM race_log WHERE race_id=? ORDER BY id DESC",
                     (race_id,)).fetchone()["message"]
    assert "2 of 40 real boats" in log and "1 waiting on the line sent off" in log
    # decided: a later correction to the fleet's tracks does not move it
    db.execute("DELETE FROM real_track"); db.commit()
    assert fleetgate.open_gate(db, race_row(db, race_id)) is None
    assert fleetgate.virtual_start(db, race_row(db, race_id)) == 1800
    assert fleetgate.fleet_gate(db, race_row(db, race_id))["open_at"] == 1800


# ---- through the API ------------------------------------------------------

def test_first_routing_waits_on_the_line_until_the_fleet_starts(client, db):
    race_id, nav, boat = api_race_with_fleet(db, 40)
    r = nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    assert r.status_code == 200
    assert r.get_json()["waiting_for_fleet"]["needed"] == 2
    state = client.get(f"/api/races/{race_id}/state").get_json()
    me = [e for e in state["entries"] if e["name"] == "Magpie"][0]
    assert me["started"] is False and me["has_route"] is True
    assert state["virtual_start"] is None and state["fleet_gate"]["started"] == 0
    mine = nav.get(f"/api/boats/{boat}").get_json()
    assert mine["sim_time"] is None and mine["route"] == [[-0.5, 0.0]]

    # the fleet gets going an hour after the gun; the read path opens the gate
    gun = race_row(db, race_id)["start_time"]
    fix(db, race_id, "R0", gun + H)
    fix(db, race_id, "R1", gun + H + 300)
    state = client.get(f"/api/races/{race_id}/state").get_json()
    assert state["virtual_start"] == gun + H + 300
    me = [e for e in state["entries"] if e["name"] == "Magpie"][0]
    assert me["started"] is True
    mine = nav.get(f"/api/boats/{boat}").get_json()
    assert mine["sim_time"] >= gun + H + 300          # sailed on from the fleet's start
    assert mine["track"][0]["t"] == gun + H + 300 + 600     # first fix one step on
    detail = client.get(f"/api/races/{race_id}").get_json()
    assert detail["virtual_start"] == gun + H + 300 and detail["fleet_start_pct"] == 5


def test_routing_after_the_gate_opened_starts_now_not_backdated(client, db):
    race_id, nav, boat = api_race_with_fleet(db, 40)
    gun = race_row(db, race_id)["start_time"]
    fix(db, race_id, "R0", gun + 60)
    fix(db, race_id, "R1", gun + 120)
    now = int(time.time())
    r = nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    assert r.get_json()["waiting_for_fleet"] is None
    mine = nav.get(f"/api/boats/{boat}").get_json()
    assert mine["sim_time"] >= now - 5


def test_boats_already_sailing_are_not_moved_when_the_gate_opens(client, db):
    race_id, nav, boat = api_race_with_fleet(db, 40)
    gun = race_row(db, race_id)["start_time"]
    # a boat that started at the gun before the gate existed
    db.execute("UPDATE boats SET sim_time=?, lat=0, lon=0, next_mark=1 WHERE id=?", (gun, boat))
    db.execute("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,0,-0.5,0.0)", (boat,))
    db.commit()
    fix(db, race_id, "R0", gun + H)
    fix(db, race_id, "R1", gun + H)
    client.get(f"/api/races/{race_id}/state")
    mine = nav.get(f"/api/boats/{boat}").get_json()
    assert mine["track"][0]["t"] == gun + 600                # first fix one step after the gun


# ---- the scripts ----------------------------------------------------------

def test_restart_boat_replays_from_the_fleet_start(db, weather, monkeypatch):
    import importlib
    race_id = race_with_fleet(db, 40, start_time=1000)
    cur = db.execute("INSERT INTO boats(race_id,name,pin_hash,created_at,sim_time,lat,lon,next_mark)"
                     " VALUES (?,?,'',1,1000,0,0,1)", (race_id, "Magpie"))
    boat = cur.lastrowid
    db.execute("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,0,-0.5,0.0)", (boat,))
    db.commit()
    restart = importlib.import_module("scripts.restart_boat")
    monkeypatch.setattr("sys.argv", ["restart_boat.py", str(race_id), "Magpie"])
    restart.main()                                    # fleet not started: waits
    assert db.execute("SELECT sim_time FROM boats WHERE id=?", (boat,)).fetchone()[0] is None
    fix(db, race_id, "R0", 1200)
    fix(db, race_id, "R1", 1800)
    restart.main()
    assert db.execute("SELECT sim_time FROM boats WHERE id=?", (boat,)).fetchone()[0] == 1800
    log = db.execute("SELECT message FROM race_log WHERE race_id=? ORDER BY id DESC",
                     (race_id,)).fetchone()["message"]
    assert "fleet's start" in log


def test_set_fleet_gate_off_sends_waiting_boats_off(db):
    import importlib
    race_id = race_with_fleet(db, 40, start_time=1000)
    cur = db.execute("INSERT INTO boats(race_id,name,pin_hash,created_at) VALUES (?,?,'',1)",
                     (race_id, "Waiting"))
    boat = cur.lastrowid
    db.execute("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,0,-0.5,0.0)", (boat,))
    db.commit()
    gate = importlib.import_module("scripts.set_fleet_gate")
    gate.main([str(race_id), "10"])
    assert race_row(db, race_id)["fleet_start_pct"] == 10
    assert fleetgate.fleet_gate(db, race_row(db, race_id))["needed"] == 4
    gate.main([str(race_id), "0"])
    assert fleetgate.fleet_gate(db, race_row(db, race_id)) is None
    t = db.execute("SELECT sim_time FROM boats WHERE id=?", (boat,)).fetchone()[0]
    assert t is not None and t >= int(time.time()) - 5
