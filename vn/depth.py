"""Water depth lookups (NOAA NCEI global DEM mosaic).

Depths are sampled in ~500 m grid cells and cached in SQLite.  A cache miss
fetches a 10×10 block of cells around the position in one getSamples call,
so a boat crossing new water costs roughly one HTTP request per 3 nm.  If
the service is unreachable the area is cached as deep water for a while —
the game must not stall on a bathymetry outage.
"""
import json
import threading
import urllib.parse
import urllib.request

GRID = 0.005                      # degrees, ~500 m
BLOCK = 10                        # cells fetched per miss (10×10)
M_TO_FT = 3.28084
DEEP_SENTINEL = 9999.0
_lock = threading.Lock()

API = ("https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/"
       "DEM_global_mosaic/ImageServer/getSamples")


def _cell(lat, lon):
    return round(round(lat / GRID) * GRID, 4), round(round(lon / GRID) * GRID, 4)


def get_depth_ft(db, lat, lon):
    """Water depth in feet at a position (positive down; negative = land).

    Returns a large value when bathymetry is unavailable, so no penalty is
    ever applied on missing data.
    """
    clat, clon = _cell(lat, lon)
    with _lock:
        row = db.execute("SELECT depth_m FROM depth_cache WHERE lat=? AND lon=?",
                         (clat, clon)).fetchone()
        if row is None:
            _fetch_block(db, clat, clon)
            row = db.execute("SELECT depth_m FROM depth_cache WHERE lat=? AND lon=?",
                             (clat, clon)).fetchone()
    if row is None:
        return DEEP_SENTINEL
    return -row["depth_m"] * M_TO_FT      # elevation (neg = below sea level) → depth


def _fetch_block(db, clat, clon):
    half = BLOCK // 2
    cells = [(round(clat + (i - half) * GRID, 4), round(clon + (j - half) * GRID, 4))
             for i in range(BLOCK) for j in range(BLOCK)]
    try:
        geometry = json.dumps({
            "points": [[lo, la] for (la, lo) in cells],
            "spatialReference": {"wkid": 4326}})
        qs = urllib.parse.urlencode({
            "geometry": geometry, "geometryType": "esriGeometryMultipoint",
            "returnFirstValueOnly": "true", "f": "json"})
        with urllib.request.urlopen(f"{API}?{qs}", timeout=20) as resp:
            data = json.loads(resp.read().decode())
        vals = {}
        for s in data.get("samples", []):
            try:
                vals[s["locationId"]] = float(s["value"])
            except (KeyError, TypeError, ValueError):
                continue
        rows = [(la, lo, vals.get(i, -DEEP_SENTINEL))
                for i, (la, lo) in enumerate(cells)]
    except Exception:
        # offline / service error: treat the block as deep water so racing
        # continues; it will be re-fetched next time the cache is cleared
        rows = [(la, lo, -DEEP_SENTINEL) for (la, lo) in cells]
    db.executemany(
        "INSERT OR IGNORE INTO depth_cache(lat,lon,depth_m) VALUES (?,?,?)", rows)
    db.commit()
