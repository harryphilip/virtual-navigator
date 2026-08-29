"""Seed a demo race so the site has something to show.

Creates a Newport→Bermuda style virtual challenge that started 36 h ago,
three virtual boats with different routings (PIN 0000), and two 'real'
tracked boats with imported positions.  Run once:

    .venv/bin/python scripts/seed_demo.py
"""
import os
import random
import secrets
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vn.db import get_db
from vn.geo import bearing_deg, destination, haversine_nm
from vn.realfleet import recompute
from vn.sim import catch_up_race, get_marks
from app import _hash_pin

NEWPORT = (41.446, -71.332)
BERMUDA = (32.380, -64.678)   # a bit N of the island so the rhumb line stays at sea


def main():
    db = get_db()
    if db.execute("SELECT 1 FROM races WHERE name LIKE 'Demo:%'").fetchone():
        print("demo race already present — nothing to do")
        return
    now = int(time.time())
    start = now - 36 * 3600
    polar_text = open(os.path.join(os.path.dirname(__file__), "..",
                                   "data", "polar_40ft.pol")).read()

    cur = db.execute(
        "INSERT INTO races(name,description,start_time,perf_factor,step_minutes,"
        "mark_radius_nm,polar_name,polar_text,admin_key,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("Demo: Newport–Bermuda Virtual Challenge",
         "635 nm offshore demo. Register a boat (any name, any PIN) or sign into "
         "a demo boat with PIN 0000. Demo admin key: demo-admin",
         start, 0.9, 10, 5.0, "Generic 40ft offshore", polar_text, "demo-admin", now))
    race_id = cur.lastrowid
    db.execute("INSERT INTO marks(race_id,seq,name,lat,lon) VALUES (?,?,?,?,?)",
               (race_id, 0, "Start — Castle Hill", *NEWPORT))
    db.execute("INSERT INTO marks(race_id,seq,name,lat,lon) VALUES (?,?,?,?,?)",
               (race_id, 1, "Finish — St. David's", *BERMUDA))

    # ---- three virtual boats with different routings -----------------------
    def midpoints(offset_deg_east, n=6):
        """Waypoints along the course, bowed east(+)/west(-)."""
        pts = []
        for i in range(1, n):
            f = i / n
            lat = NEWPORT[0] + f * (BERMUDA[0] - NEWPORT[0])
            lon = NEWPORT[1] + f * (BERMUDA[1] - NEWPORT[1])
            bow = offset_deg_east * (1 - abs(2 * f - 1))   # max bow mid-course
            pts.append((round(lat, 4), round(lon + bow, 4)))
        pts.append(BERMUDA)
        return pts

    fleets = [
        ("Rhumb Runner", midpoints(0.0)),
        ("Gulf Stream Gambler", midpoints(+1.2)),
        ("Westabout", midpoints(-1.0)),
    ]
    for name, wps in fleets:
        cur = db.execute(
            "INSERT INTO boats(race_id,name,pin_hash,created_at,sim_time,lat,lon,next_mark) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (race_id, name, _hash_pin("0000"), start, start, *NEWPORT))
        boat_id = cur.lastrowid
        for i, (la, lo) in enumerate(wps):
            db.execute("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,?,?,?)",
                       (boat_id, i, la, lo))
        db.execute("INSERT INTO route_log(boat_id,submitted_at,wp_json) VALUES (?,?,?)",
                   (boat_id, start, "[]"))

    # ---- two 'real' boats with synthetic tracker imports -------------------
    rnd = random.Random(7)
    for name, klass, kn, drift in [("Wild Horses", "Same polar (40ft)", 8.1, +0.10),
                                   ("Restless", "TP52", 9.6, -0.12)]:
        cur = db.execute("INSERT INTO real_boats(race_id,name,klass) VALUES (?,?,?)",
                         (race_id, name, klass))
        rb_id = cur.lastrowid
        lat, lon = NEWPORT
        t = start
        rows = []
        while t <= now:
            rows.append((rb_id, t, round(lat, 5), round(lon, 5)))
            brg = bearing_deg(lat, lon, *BERMUDA) + drift * 25 * rnd.uniform(0.3, 1.0)
            spd = max(4.0, kn + rnd.uniform(-1.8, 1.8))
            lat, lon = destination(lat, lon, brg, spd * 0.5)   # 30-min fixes
            t += 1800
            if haversine_nm(lat, lon, *BERMUDA) < 3:
                rows.append((rb_id, t, *BERMUDA))
                break
        db.executemany("INSERT INTO real_track(rb_id,t,lat,lon) VALUES (?,?,?,?)", rows)

    db.commit()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    marks = get_marks(db, race_id)
    for rb in db.execute("SELECT id FROM real_boats WHERE race_id=?", (race_id,)).fetchall():
        recompute(db, race, marks, rb["id"])
    db.commit()
    print(f"seeded race {race_id}; simulating 36 h of racing (fetching wind)…")
    t0 = time.time()
    catch_up_race(db, race_id, now)
    src = db.execute("SELECT source, COUNT(*) c FROM wind_cache GROUP BY source").fetchall()
    print(f"done in {time.time() - t0:.1f}s; wind cache: "
          + ", ".join(f"{r['source']}={r['c']}" for r in src))
    for b in db.execute("SELECT name, lat, lon, finished_at FROM boats WHERE race_id=?",
                        (race_id,)):
        d = haversine_nm(b["lat"], b["lon"], *BERMUDA)
        print(f"  {b['name']:22s} {d:6.1f} nm to finish"
              + ("  FINISHED" if b["finished_at"] else ""))


if __name__ == "__main__":
    main()
