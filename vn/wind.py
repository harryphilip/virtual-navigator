"""Wind provider.

Primary source: Open-Meteo (free, no key) 10 m wind, hourly, covering the past
few days and the forecast ahead — the server always evaluates boats against
the same weather everyone else gets.  Values are cached in SQLite on a 0.25°
grid so each grid cell is fetched at most once per series window.

If the machine is offline (or the API fails) a deterministic synthetic wind
field is used instead so races still run; the source is recorded per sample.
"""
import datetime as dt
import json
import math
import threading
import urllib.request

GRID = 0.25          # degrees
_lock = threading.Lock()

API = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
       "&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=kn"
       "&past_days=7&forecast_days=7&timeformat=unixtime")


def _cell(lat, lon):
    return round(round(lat / GRID) * GRID, 4), round(round(lon / GRID) * GRID, 4)


def get_wind(db, lat, lon, t_unix):
    """Return (twd_from_deg, tws_kn, source) at position/time."""
    clat, clon = _cell(lat, lon)
    hour = int(t_unix // 3600) * 3600
    with _lock:
        rows = db.execute(
            "SELECT t, twd, tws, source FROM wind_cache WHERE lat=? AND lon=? "
            "AND t IN (?, ?)", (clat, clon, hour, hour + 3600)).fetchall()
        have = {r["t"]: r for r in rows}
        if hour not in have or (hour + 3600) not in have:
            _fetch_cell(db, clat, clon)
            rows = db.execute(
                "SELECT t, twd, tws, source FROM wind_cache WHERE lat=? AND lon=? "
                "AND t IN (?, ?)", (clat, clon, hour, hour + 3600)).fetchall()
            have = {r["t"]: r for r in rows}

    a, b = have.get(hour), have.get(hour + 3600)
    if a and b:
        f = (t_unix - hour) / 3600.0
        twd = _lerp_angle(a["twd"], b["twd"], f)
        tws = a["tws"] + f * (b["tws"] - a["tws"])
        return twd, tws, a["source"]
    if a or b:
        r = a or b
        return r["twd"], r["tws"], r["source"]
    return _synthetic(lat, lon, t_unix) + ("synthetic",)


def _fetch_cell(db, clat, clon):
    """Fetch the full hourly series for one grid cell; fall back to synthetic."""
    try:
        url = API.format(lat=clat, lon=clon)
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        hh = data["hourly"]
        rows = []
        for t, spd, deg in zip(hh["time"], hh["wind_speed_10m"], hh["wind_direction_10m"]):
            if spd is None or deg is None:
                continue
            rows.append((clat, clon, int(t), float(deg), float(spd), "open-meteo"))
        db.executemany(
            "INSERT OR REPLACE INTO wind_cache(lat,lon,t,twd,tws,source) "
            "VALUES (?,?,?,?,?,?)", rows)
        db.commit()
    except Exception:
        # offline / API error: pre-fill this cell with synthetic wind for ±7 days
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        rows = []
        for h in range(-7 * 24, 7 * 24):
            t = (now // 3600 + h) * 3600
            twd, tws = _synthetic(clat, clon, t)
            rows.append((clat, clon, t, twd, tws, "synthetic"))
        db.executemany(
            "INSERT OR IGNORE INTO wind_cache(lat,lon,t,twd,tws,source) "
            "VALUES (?,?,?,?,?,?)", rows)
        db.commit()


MARINE_API = ("https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}"
              "&hourly=ocean_current_velocity,ocean_current_direction"
              "&past_days=7&forecast_days=5&timeformat=unixtime")
KMH_TO_KN = 1.0 / 1.852


def get_current(db, lat, lon, t_unix):
    """Surface current (set_toward_deg, drift_kn, source) at position/time.

    Same grid/caching scheme as wind.  Land cells, missing data, or an
    unreachable marine API all yield zero current.
    """
    clat, clon = _cell(lat, lon)
    hour = int(t_unix // 3600) * 3600
    with _lock:
        rows = db.execute(
            "SELECT t, cdir, cspd, source FROM current_cache WHERE lat=? AND lon=? "
            "AND t IN (?, ?)", (clat, clon, hour, hour + 3600)).fetchall()
        have = {r["t"]: r for r in rows}
        if hour not in have or (hour + 3600) not in have:
            _fetch_current_cell(db, clat, clon)
            rows = db.execute(
                "SELECT t, cdir, cspd, source FROM current_cache WHERE lat=? AND lon=? "
                "AND t IN (?, ?)", (clat, clon, hour, hour + 3600)).fetchall()
            have = {r["t"]: r for r in rows}
    a, b = have.get(hour), have.get(hour + 3600)
    if a and b:
        f = (t_unix - hour) / 3600.0
        return (_lerp_angle(a["cdir"], b["cdir"], f),
                a["cspd"] + f * (b["cspd"] - a["cspd"]), a["source"])
    if a or b:
        r = a or b
        return r["cdir"], r["cspd"], r["source"]
    return 0.0, 0.0, "none"


def _fetch_current_cell(db, clat, clon):
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    rows = []
    try:
        url = MARINE_API.format(lat=clat, lon=clon)
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        hh = data["hourly"]
        for t, spd, deg in zip(hh["time"], hh["ocean_current_velocity"],
                               hh["ocean_current_direction"]):
            rows.append((clat, clon, int(t),
                         float(deg or 0.0), float(spd or 0.0) * KMH_TO_KN,
                         "open-meteo"))
    except Exception:
        # no marine data here (land cell / offline): zero current, cached so
        # we don't retry every step
        for h in range(-7 * 24, 5 * 24):
            t = (now // 3600 + h) * 3600
            rows.append((clat, clon, t, 0.0, 0.0, "none"))
    db.executemany(
        "INSERT OR IGNORE INTO current_cache(lat,lon,t,cdir,cspd,source) "
        "VALUES (?,?,?,?,?,?)", rows)
    db.commit()


def _synthetic(lat, lon, t_unix):
    """Smooth deterministic wind field: everyone sees the same weather."""
    th = t_unix / 3600.0
    twd = (230.0
           + 45.0 * math.sin(th / 17.0 + lat * 0.20)
           + 25.0 * math.sin(th / 5.5 + lon * 0.15)) % 360.0
    tws = (14.0
           + 6.0 * math.sin(th / 11.0 + lon * 0.25)
           + 4.0 * math.sin(th / 3.7 + lat * 0.33))
    return twd, max(2.0, tws)


def _lerp_angle(a, b, f):
    d = ((b - a + 540.0) % 360.0) - 180.0
    return (a + f * d) % 360.0
