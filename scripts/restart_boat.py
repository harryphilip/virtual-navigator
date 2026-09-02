"""Restart a virtual boat from the race's (possibly corrected) start line.

    .venv/bin/python scripts/restart_boat.py <race_id> <boat name>

On Fly:  fly ssh console -C "python /app/scripts/restart_boat.py 3 Magpie"

Wipes the boat's sailed track and counters, re-arms its full submitted
routing, reconciles it against the current course from the start mark
(the same soft join a mid-race resubmission gets), and re-anchors the
boat on the line at the gun.  The next tick replays the race so far
through the same cached weather — deterministic, nothing invented.
Route submission history is kept.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db
from vn.sim import enforce_course, get_marks


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    race_id, name = int(sys.argv[1]), sys.argv[2]
    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    b = db.execute("SELECT * FROM boats WHERE race_id=? AND name=?",
                   (race_id, name)).fetchone()
    if not (race and b):
        print("race or boat not found")
        sys.exit(1)
    marks = get_marks(db, race_id)

    db.execute("DELETE FROM track WHERE boat_id=?", (b["id"],))
    db.execute("UPDATE route_wps SET passed=0 WHERE boat_id=?", (b["id"],))
    wps = [(r["lat"], r["lon"]) for r in db.execute(
        "SELECT lat,lon FROM route_wps WHERE boat_id=? ORDER BY seq", (b["id"],))]
    start = (marks[0]["lat"], marks[0]["lon"])
    wps, notes = enforce_course(wps, marks, 1, race["mark_radius_nm"], start)
    db.execute("DELETE FROM route_wps WHERE boat_id=?", (b["id"],))
    db.executemany("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,?,?,?)",
                   [(b["id"], i, la, lo) for i, (la, lo) in enumerate(wps)])
    db.execute(
        "UPDATE boats SET sim_time=?, lat=?, lon=?, next_mark=1, finished_at=NULL,"
        " wind_side=NULL, maneuvers=0, groundings=0, zone_steps=0 WHERE id=?",
        (race["start_time"], start[0], start[1], b["id"]))
    db.commit()
    print(f"{name}: restarted on the line at {start[0]:.4f},{start[1]:.4f}, "
          f"{len(wps)} waypoint(s) re-armed — replay begins next tick")
    for n in notes:
        print("  •", n)


if __name__ == "__main__":
    main()
