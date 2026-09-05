"""Restart a virtual boat from the race's (possibly corrected) start line.

    .venv/bin/python scripts/restart_boat.py <race_id> <boat name>

On Fly:  fly ssh console -C "python /app/scripts/restart_boat.py 3 Magpie"

Wipes the boat's sailed track and counters, re-arms its full submitted
routing, reconciles it against the current course from the start mark
(the same soft join a mid-race resubmission gets), detours the result
around any exclusion zones (the join itself can create a crossing leg),
and re-anchors the boat on the line at the virtual start — the gun, or
the real fleet's start where the race waits for it (vn/fleetgate.py); a
boat restarted while the fleet has not started waits on the line for it.
The next tick replays
the race so far through recorded weather: real wind is refetched back to
92 days (the API's archive limit); anything older sails placeholder wind,
and every fix on the track records which it was (audit_replay.py reads
it back).  Route submission history is kept.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db
from vn.detour import route_around_zones, smart_join
from vn.fleetgate import virtual_start
from vn.sim import enforce_course, get_marks, race_zones


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
    zones = race_zones(race)
    if zones and len(marks) == 2:
        # no intermediate roundings: free to pick up the routing wherever
        # a zone-clean sail from the line reaches it cheapest
        wps, znotes = smart_join(start, wps, zones)
        wps = wps[1:]                     # the line itself stays the anchor
        notes += znotes
    elif zones:
        wps, touched = route_around_zones([start] + wps, zones)
        wps = wps[1:]                     # the line itself stays the anchor
        for zname, cut, ins in touched:
            notes.append("The route still brushes a zone; check the chart"
                         if zname == "!unresolved" else
                         f"{zname}: {cut} waypoint{'s' if cut != 1 else ''} detoured via "
                         f"{ins} boundary point{'s' if ins != 1 else ''}")
    db.execute("DELETE FROM route_wps WHERE boat_id=?", (b["id"],))
    db.executemany("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,?,?,?)",
                   [(b["id"], i, la, lo) for i, (la, lo) in enumerate(wps)])
    vs = virtual_start(db, race)
    db.execute(
        "UPDATE boats SET sim_time=?, lat=?, lon=?, next_mark=1, finished_at=NULL,"
        " wind_side=NULL, maneuvers=0, groundings=0, zone_steps=0 WHERE id=?",
        (vs, start[0], start[1], b["id"]))
    add_race_log(db, race_id,
                 f"{name} restarted from the line and "
                 + ("waits there for the real fleet to start." if vs is None else
                    "replayed from the gun through recorded weather."
                    if vs == race["start_time"] else
                    "replayed from the fleet's start through recorded weather.")
                 + ("".join(" " + n[0].upper() + n[1:].rstrip(".") + "." for n in notes)
                    if notes else ""))
    db.commit()
    print(f"{name}: restarted on the line at {start[0]:.4f},{start[1]:.4f}, "
          f"{len(wps)} waypoint(s) re-armed — "
          + ("waits for the fleet to start" if vs is None else "replay begins next tick"))
    for n in notes:
        print("  •", n)


if __name__ == "__main__":
    main()
