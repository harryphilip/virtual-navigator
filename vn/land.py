"""Is that point on land?  Natural Earth land polygons, shipped with the
repo (data/ne_*_land.geojson, public domain), so a submitted routing can
be checked for land crossings offline, in milliseconds, before the engine
sails it into a headland.

The engine itself does no land avoidance: it drags through water shallower
than the race's grounding depth at half speed and sails over dry land the
same way, which makes a route through Sicily a very slow route rather than
an impossible one. This check is for the navigator's benefit at submission:
a crossing longer than REJECT_NM is refused with the leg named; shorter
ones (a harbour wall, an islet the polygons draw fat) come back as a
warning. Resolution is the 1:50m data set, about a kilometre at the coast,
so small islands and harbour geometry are approximate.
"""
import glob
import json
import os
import threading

from .geo import destination, bearing_deg, haversine_nm

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
REJECT_NM = 5.0                   # a crossing longer than this is refused
STEP_NM = 1.0                     # sample spacing along a leg

_lock = threading.Lock()
_polys = None                     # [(minlat, maxlat, minlon, maxlon, ring, exterior), ...]
_grid = None                      # (lat//CELL, lon//CELL) -> polys whose box touches the cell
CELL = 5                          # degrees


def _load():
    global _polys, _grid
    if _polys is not None:
        return _polys
    with _lock:
        if _polys is not None:
            return _polys
        files = sorted(glob.glob(os.path.join(DATA_DIR, "ne_*_land.geojson")))
        polys = []
        if files:
            with open(files[0], encoding="utf-8") as f:
                gj = json.load(f)
            for feat in gj.get("features", []):
                geom = feat.get("geometry") or {}
                shapes = ([geom["coordinates"]] if geom.get("type") == "Polygon"
                          else geom.get("coordinates", []) if geom.get("type") == "MultiPolygon"
                          else [])
                for shape in shapes:
                    for k, ring in enumerate(shape):
                        lons = [p[0] for p in ring]
                        lats = [p[1] for p in ring]
                        # exterior rings are land, holes (k > 0) are water inside it
                        polys.append((min(lats), max(lats), min(lons), max(lons),
                                      ring, k == 0))
        grid = {}
        for p in polys:
            for i in range(int(p[0] // CELL), int(p[1] // CELL) + 1):
                for j in range(int(p[2] // CELL), int(p[3] // CELL) + 1):
                    grid.setdefault((i, j), []).append(p)
        _grid = grid
        _polys = polys
        return polys


def _candidates(lat, lon):
    _load()
    return _grid.get((int(lat // CELL), int(lon // CELL)), ())


def available():
    return bool(_load())


def _inside(ring, lat, lon):
    """Ray casting, ring as [[lon, lat], ...]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x:
                inside = not inside
        j = i
    return inside


def is_land(lat, lon):
    hit = False
    for minlat, maxlat, minlon, maxlon, ring, exterior in _candidates(lat, lon):
        if not (minlat <= lat <= maxlat and minlon <= lon <= maxlon):
            continue
        if _inside(ring, lat, lon):
            if exterior:
                hit = True
            else:
                return False          # inside a hole: a lake or inland sea
    return hit


def leg_land_nm(a, b, step_nm=STEP_NM):
    """Nautical miles of land along the leg a→b (lat, lon pairs), sampled
    every step_nm, and the first land sample met (or None)."""
    dist = haversine_nm(a[0], a[1], b[0], b[1])
    if dist == 0:
        return (step_nm if is_land(a[0], a[1]) else 0.0), (a if is_land(a[0], a[1]) else None)
    brg = bearing_deg(a[0], a[1], b[0], b[1])
    n = max(1, int(dist / step_nm))
    land = 0.0
    first = None
    for i in range(n + 1):
        d = min(dist, i * step_nm)
        lat, lon = destination(a[0], a[1], brg, d)
        if is_land(lat, lon):
            land += step_nm if i < n else max(0.0, dist - (n - 1) * step_nm)
            if first is None:
                first = (round(lat, 3), round(lon, 3))
    return min(land, dist), first


MAX_POINTS = 4000                 # a 10,000-waypoint upload is thinned before sampling


def crossings(points, step_nm=STEP_NM):
    """[{leg, from, to, land_nm, at}] for every leg of a routing (a list of
    (lat, lon), the boat's position first) that touches land. Leg numbers
    refer to the list as given; a very dense routing is thinned to
    MAX_POINTS first (every k-th point plus the last), which costs nothing
    at the 1 nm sampling step."""
    if len(points) > MAX_POINTS:
        k = -(-len(points) // MAX_POINTS)
        kept = list(range(0, len(points), k))
        if kept[-1] != len(points) - 1:
            kept.append(len(points) - 1)
    else:
        kept = list(range(len(points)))
    out = []
    for n in range(1, len(kept)):
        i, j = kept[n - 1], kept[n]
        land, first = leg_land_nm(points[i], points[j], step_nm)
        if land > 0:
            out.append({"leg": j, "from": points[i], "to": points[j],
                        "land_nm": round(land, 1), "at": first})
    return out


def _fmt(p):
    lat, lon = p
    return f"{abs(lat):.2f}{'N' if lat >= 0 else 'S'} {abs(lon):.2f}{'E' if lon >= 0 else 'W'}"


def describe(c):
    return (f"leg {c['leg']} ({_fmt(c['from'])} to {_fmt(c['to'])}) crosses about "
            f"{c['land_nm']:g} nm of land near {_fmt(c['at'])}")
