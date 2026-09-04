"""Detour a boat's routing around the race's exclusion zones.

    .venv/bin/python scripts/reroute_zones.py <race_id> <boat name> [margin_nm]

On Fly:  fly ssh console -C "python /app/scripts/reroute_zones.py 3 Magpie"

For routings that predate the zones (rules changed under way, and a boat
shouldn't be punished for information it didn't have): every stretch of the
routing inside a zone is replaced with the cheaper way around it — see
vn/detour.py. Rewrites the whole routing and clears passed flags, so ALWAYS
follow with scripts/restart_boat.py (which also detours after its course
join, so for a restart this script is optional).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db
from vn.detour import route_around_zones
from vn.geo import haversine_nm
from vn.sim import race_zones


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    race_id, name = int(sys.argv[1]), sys.argv[2]
    margin = float(sys.argv[3]) if len(sys.argv) == 4 else 3.0
    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    b = db.execute("SELECT * FROM boats WHERE race_id=? AND name=?",
                   (race_id, name)).fetchone()
    if not (race and b):
        print("race or boat not found")
        sys.exit(1)
    zones = race_zones(race)
    if not zones:
        print("race has no zones")
        sys.exit(1)
    wps = [(r["lat"], r["lon"]) for r in db.execute(
        "SELECT lat,lon FROM route_wps WHERE boat_id=? ORDER BY seq", (b["id"],))]
    before = sum(haversine_nm(*wps[i], *wps[i + 1]) for i in range(len(wps) - 1))
    wps, touched = route_around_zones(wps, zones, margin)
    if not touched:
        print(f"{name}: routing is already clear of all "
              f"{len(zones)} zone(s) — nothing changed")
        return
    db.execute("DELETE FROM route_wps WHERE boat_id=?", (b["id"],))
    db.executemany("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,?,?,?)",
                   [(b["id"], s, la, lo) for s, (la, lo) in enumerate(wps)])
    add_race_log(db, race_id,
                 f"{name}'s routing detoured around exclusion zones "
                 "(routing predated them): "
                 + "; ".join(f"{z} ({cut} wp → {ins})"
                             for z, cut, ins in touched if z != "!unresolved")
                 + ".")
    db.commit()
    after = sum(haversine_nm(*wps[i], *wps[i + 1]) for i in range(len(wps) - 1))
    print(f"{name}: {len(wps)} waypoint(s), route {before:.0f} -> {after:.0f} nm "
          f"({after - before:+.0f})")
    for zname, cut, ins in touched:
        print("  routing still brushes a zone — check the chart"
              if zname == "!unresolved" else
              f"  {zname}: {cut} waypoint(s) inside replaced with "
              f"{ins} boundary point(s)")
    print("now run restart_boat.py to replay from the gun on the clean route")


if __name__ == "__main__":
    main()
