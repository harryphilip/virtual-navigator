"""Per-race 'on-board' forecast snapshots.

Every few hours the ticker captures the wind forecast over the race area —
the same Open-Meteo model the engine will later sail boats through — and
stores it as a real GRIB-1 file.  Competitors download the snapshot into
their routing software; the archive of snapshots is exactly the sequence of
forecasts that was available on board, so routings can be replayed honestly
after the finish.
"""
import datetime as dt
import json
import math
import time
import urllib.request

from . import quota
from .grib import read_messages, wind_grib

FORECAST_HOURS = list(range(0, 121, 3))
MAX_POINTS = 240
BATCH = 60
KN_TO_MS = 0.514444
MS_TO_KN = 1.0 / KN_TO_MS

API = ("https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}"
       "&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms"
       "&forecast_days=6&timeformat=unixtime")


def grid_for_race(marks, pad=1.5):
    lats = [m["lat"] for m in marks]
    lons = [m["lon"] for m in marks]
    la_n, la_s = max(lats) + pad, min(lats) - pad
    lo_w, lo_e = min(lons) - pad, max(lons) + pad
    for step in (0.25, 0.5, 1.0, 2.0, 4.0):
        ni = int((lo_e - lo_w) / step) + 1
        nj = int((la_n - la_s) / step) + 1
        if ni * nj <= MAX_POINTS:
            return round(la_n, 2), round(lo_w, 2), step, ni, nj
    return round(la_n, 2), round(lo_w, 2), 8.0, ni, nj


def make_snapshot(db, race):
    """Fetch the current forecast for the race area and store it as GRIB."""
    marks = db.execute("SELECT * FROM marks WHERE race_id=? ORDER BY seq",
                       (race["id"],)).fetchall()
    la1, lo1, step, ni, nj = grid_for_race(marks)
    points = [(round(la1 - j * step, 3), round(lo1 + i * step, 3))
              for j in range(nj) for i in range(ni)]     # N→S rows, W→E cols

    issued = int(time.time()) // 3600 * 3600
    series = _fetch_batches(points)

    frames = []
    for fh in FORECAST_HOURS:
        t_valid = issued + fh * 3600
        u, v = [], []
        ok = 0
        for p in series:
            spd, deg = p.get(t_valid, (None, None))
            if spd is None:
                u.append(0.0)
                v.append(0.0)
            else:
                rad = math.radians(deg)
                u.append(-spd * math.sin(rad))
                v.append(-spd * math.cos(rad))
                ok += 1
        if ok == 0:
            break                     # past the end of the model run
        frames.append((fh, u, v))
    if not frames:
        raise RuntimeError("forecast fetch produced no data")

    ref = dt.datetime.fromtimestamp(issued, dt.timezone.utc)
    blob = wind_grib(ref, la1, lo1, step, ni, nj, frames)
    meta = {"la1": la1, "lo1": lo1, "step": step, "ni": ni, "nj": nj,
            "hours": [f[0] for f in frames], "bytes": len(blob)}
    cur = db.execute(
        "INSERT INTO forecast_snapshots(race_id,issued_at,meta_json,grib) "
        "VALUES (?,?,?,?)", (race["id"], issued, json.dumps(meta), blob))
    db.commit()
    return cur.lastrowid


def _fetch_batches(points):
    """One dict {valid_time: (speed_ms, dir_deg)} per requested point."""
    series = []
    for i in range(0, len(points), BATCH):
        chunk = points[i:i + BATCH]
        url = API.format(lats=",".join(str(p[0]) for p in chunk),
                         lons=",".join(str(p[1]) for p in chunk))
        quota.note_calls("forecast", len(chunk))
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if isinstance(data, dict):
            data = [data]
        for loc in data:
            hh = loc.get("hourly", {})
            m = {}
            for t, spd, deg in zip(hh.get("time", []),
                                   hh.get("wind_speed_10m", []),
                                   hh.get("wind_direction_10m", [])):
                if spd is not None and deg is not None:
                    m[int(t)] = (float(spd), float(deg))
            series.append(m)
    return series


def latest_field(db, race_id):
    """The newest snapshot for a race decoded back into a wind field the
    chart can draw: the grid corner and spacing, and for every forecast
    hour the valid time and u/v components in knots, row-major from the
    north-west corner (west→east, then north→south — the GRIB's own order).
    None when the race has no snapshot yet."""
    row = db.execute(
        "SELECT id, issued_at, grib FROM forecast_snapshots WHERE race_id=? "
        "ORDER BY issued_at DESC, id DESC LIMIT 1", (race_id,)).fetchone()
    if row is None:
        return None
    return decode_field(row["id"], row["issued_at"], row["grib"])


def decode_field(snap_id, issued, blob):
    """Read our own GRIB back (vn.grib.read_messages) and pair the U and V
    messages of each forecast hour."""
    by_hour = {}
    grid = None
    for param, p1, la1, lo1, step, ni, nj, values in read_messages(blob):
        grid = grid or {"la1": la1, "lo1": lo1, "step": step, "ni": ni, "nj": nj}
        by_hour.setdefault(p1, {})[param] = values
    frames = []
    for fh in sorted(by_hour):
        u, v = by_hour[fh].get(33), by_hour[fh].get(34)
        if u is None or v is None:
            continue
        frames.append({"fh": fh, "t": issued + fh * 3600,
                       "u": [round(x * MS_TO_KN, 1) for x in u],
                       "v": [round(x * MS_TO_KN, 1) for x in v]})
    if grid is None or not frames:
        return None
    return {"id": snap_id, "issued_at": issued, **grid, "frames": frames}
