"""Detour routings around exclusion zones.

Every stretch of a routing inside a zone is replaced with the cheaper way
around it — the zone's own boundary vertices, pushed a margin outside,
walked in whichever direction adds less distance. Used when the routing
predates the zones (rules changed under way) and after mechanical route
edits (mid-race joins) that can create a crossing leg.
"""
from .geo import bearing_deg, destination, haversine_nm, point_in_poly


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


def _chain(ring, i, j, forward):
    out, k, n = [], i, len(ring)
    while True:
        out.append(ring[k])
        if k == j:
            return out
        k = (k + 1) % n if forward else (k - 1) % n


def _detour(entry, exit_, zone, all_zones, margin_nm):
    """Cheapest boundary walk whose on/off legs verifiably clear the zones.

    Nearest-vertex joins fail on sliver zones (the leg to the nearest
    vertex can itself cross), so every entry/exit vertex pair is tried and
    only pairs with clean connecting legs compete on distance. Legs are
    checked against every zone, not just this one — zones can abut, and a
    walk around one must not dive into its neighbour.
    """
    ring = offset_poly(zone["pts"], margin_nm)
    n = len(ring)

    def cost(path):
        legs = [entry] + path + [exit_]
        return sum(haversine_nm(legs[i][0], legs[i][1],
                                legs[i + 1][0], legs[i + 1][1])
                   for i in range(len(legs) - 1))

    def clean(a, b):
        return not any(leg_hits(a, b, z["pts"], 0.25) for z in all_zones)
    ok_on = [clean(entry, ring[a]) for a in range(n)]
    ok_off = [clean(ring[b], exit_) for b in range(n)]
    best, best_cost = None, float("inf")
    for a in range(n):
        if not ok_on[a]:
            continue
        for b in range(n):
            if not ok_off[b]:
                continue
            for fwd in (True, False):
                path = _chain(ring, a, b, fwd)
                c = cost(path)
                if c < best_cost:
                    best, best_cost = path, c
    if best is None:                     # no clean join — nearest vertices
        a = min(range(n), key=lambda i: haversine_nm(
            entry[0], entry[1], ring[i][0], ring[i][1]))
        b = min(range(n), key=lambda i: haversine_nm(
            exit_[0], exit_[1], ring[i][0], ring[i][1]))
        best = min((_chain(ring, a, b, True), _chain(ring, a, b, False)),
                   key=cost)
    return best


def route_around_zones(wps, zones, margin_nm=3.0, rounds=8, step_nm=0.25):
    """Rewrite waypoints [(lat, lon), ...] to clear every zone.

    Legs are sampled every step_nm — fine enough to catch a long leg
    skimming a sliver zone at a shallow angle. Returns (waypoints, touched)
    where touched lists (zone name, waypoints removed, boundary points
    inserted). Converges or returns with a final entry ('!unresolved', 0, 0)
    so callers can warn.
    """
    wps = list(wps)
    touched = []
    for _ in range(rounds):
        dirty = False
        for z in zones:
            i = 0
            while i < len(wps) - 1:
                if not (point_in_poly(wps[i + 1][0], wps[i + 1][1], z["pts"])
                        or leg_hits(wps[i], wps[i + 1], z["pts"], step_nm)):
                    i += 1
                    continue
                j = i + 1                       # walk to the far side
                while j < len(wps) - 1 and (
                        point_in_poly(wps[j][0], wps[j][1], z["pts"])
                        or leg_hits(wps[j], wps[j + 1], z["pts"], step_nm)):
                    j += 1
                path = _detour(wps[i], wps[min(j + 1, len(wps) - 1)],
                               z, zones, margin_nm)
                wps[i + 1:j + 1] = path
                touched.append((z["name"], j - i, len(path)))
                dirty = True
                i += len(path) + 1
        if not dirty:
            return wps, touched
    touched.append(("!unresolved", 0, 0))
    return wps, touched
