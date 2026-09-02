"""Detour a boat's routing around the race's exclusion zones.

    .venv/bin/python scripts/reroute_zones.py <race_id> <boat name> [margin_nm]

On Fly:  fly ssh console -C "python /app/scripts/reroute_zones.py 3 Magpie"

For fleets whose routing predates the zones (rules changed under way, and a
boat shouldn't be punished for information it didn't have): every stretch of
the routing inside a zone is replaced with the cheaper way around it — the
zone's own boundary vertices, pushed `margin_nm` (default 3) outside, walked
in whichever direction adds less distance. The rest of the routing is
untouched. Run scripts/restart_boat.py afterwards to replay the race as if
the boat had sailed the detour from the gun.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db
from vn.geo import bearing_deg, destination, haversine_nm, point_in_poly
from vn.sim import race_zones


def offset_poly(pts, margin_nm):
    """Polygon vertices pushed outward from the centroid, margin extra."""
    cy = sum(p[0] for p in pts) / len(pts)
    cx = sum(p[1] for p in pts) / len(pts)
    out = []
    for la, lo in pts:
        brg = bearing_deg(cy, cx, la, lo)
        q = destination(la, lo, brg, margin_nm)
        # elongated slivers can leave a centroid-pushed point still inside —
        # keep nudging until it tests clean
        for _ in range(8):
            if not point_in_poly(q[0], q[1], pts):
                break
            q = destination(q[0], q[1], brg, margin_nm)
        out.append(q)
    return out


def leg_hits(a, b, pts, step_nm=1.0):
    """True if the straight leg a→b passes through the polygon."""
    d = haversine_nm(a[0], a[1], b[0], b[1])
    n = max(1, int(d / step_nm))
    for i in range(n + 1):
        f = i / n
        la = a[0] + (b[0] - a[0]) * f
        lo = a[1] + (b[1] - a[1]) * f
        if point_in_poly(la, lo, pts):
            return True
    return False


def chain(ring, i, j, forward):
    """Vertices from index i to j walking the ring in one direction."""
    out, k, n = [], i, len(ring)
    while True:
        out.append(ring[k])
        if k == j:
            return out
        k = (k + 1) % n if forward else (k - 1) % n


def detour(entry, exit_, zone, margin_nm):
    ring = offset_poly(zone["pts"], margin_nm)
    a = min(range(len(ring)), key=lambda i: haversine_nm(
        entry[0], entry[1], ring[i][0], ring[i][1]))
    b = min(range(len(ring)), key=lambda i: haversine_nm(
        exit_[0], exit_[1], ring[i][0], ring[i][1]))

    def cost(path):
        legs = [entry] + path + [exit_]
        return sum(haversine_nm(legs[i][0], legs[i][1],
                                legs[i + 1][0], legs[i + 1][1])
                   for i in range(len(legs) - 1))
    fwd, back = chain(ring, a, b, True), chain(ring, a, b, False)
    return min((fwd, back), key=cost)


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

    touched = []
    for _round in range(6):
        dirty = False
        for z in zones:
            i = 0
            while i < len(wps) - 1:
                if not (point_in_poly(wps[i + 1][0], wps[i + 1][1], z["pts"])
                        or leg_hits(wps[i], wps[i + 1], z["pts"])):
                    i += 1
                    continue
                j = i + 1                       # walk to the far side
                while j < len(wps) - 1 and (
                        point_in_poly(wps[j][0], wps[j][1], z["pts"])
                        or leg_hits(wps[j], wps[j + 1], z["pts"])):
                    j += 1
                path = detour(wps[i], wps[min(j + 1, len(wps) - 1)], z, margin)
                wps[i + 1:j + 1] = path
                touched.append((z["name"], j - i, len(path)))
                dirty = True
                i += len(path) + 1
        if not dirty:
            break
    else:
        print("warning: routing still brushes a zone after 6 passes — "
              "check the map")

    if not touched:
        print(f"{name}: routing is already clear of all "
              f"{len(zones)} zone(s) — nothing changed")
        return
    db.execute("DELETE FROM route_wps WHERE boat_id=?", (b["id"],))
    db.executemany("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,?,?,?)",
                   [(b["id"], s, la, lo) for s, (la, lo) in enumerate(wps)])
    db.commit()
    after = sum(haversine_nm(*wps[i], *wps[i + 1]) for i in range(len(wps) - 1))
    print(f"{name}: {len(wps)} waypoint(s), route {before:.0f} -> {after:.0f} nm "
          f"({after - before:+.0f})")
    for zname, cut, ins in touched:
        print(f"  ⛔ {zname}: {cut} waypoint(s) inside replaced with "
              f"{ins} boundary point(s)")
    print("now run restart_boat.py to replay from the gun on the clean route")


if __name__ == "__main__":
    main()
