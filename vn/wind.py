"""Wind provider.

Primary source: Open-Meteo (free, no key) 10 m wind, hourly, covering the past
few days and the forecast ahead — the server always evaluates boats against
the same weather everyone else gets.  Values are cached in SQLite on a 0.25°
grid so each grid cell is fetched at most once per series window.

If the machine is offline (or the API fails) a deterministic synthetic wind
field is used instead so races still run; the source is recorded per sample.
Placeholder rows cover only a short window around now, and the ticker calls
heal_fallback() every minute to replace them with real data as soon as the
API answers again — a boat crossing the cell is not needed for it to heal.
"""
import json
import math
import threading
import time
import urllib.request

GRID = 0.25          # degrees
_lock = threading.Lock()
_retry = {}          # (kind, cell) -> last fallback-refetch attempt, unix
_failed = {}         # (kind, cell) -> last failed fetch, unix
RETRY_SECONDS = 900
FAIL_FILL_HOURS = (-12, 24)   # placeholder rows written after a failed fetch
LIVE_WINDOW = (-6 * 3600, 24 * 3600)   # what "sailing real weather now" spans


def _retry_due(kind, clat, clon):
    """Fallback data (synthetic wind, zero current) is cached so races keep
    running offline, but it must heal: a cell serving fallback values gets
    the real API retried at most once per RETRY_SECONDS — one rate-limited
    fetch must not becalm a boat in fake weather for a week."""
    key = (kind, clat, clon)
    now = time.time()
    if now - _retry.get(key, 0) < RETRY_SECONDS:
        return False
    _retry[key] = now
    return True


def _fetch_allowed(kind, clat, clon, stale):
    """A cell that recently failed, or that holds fallback rows, is fetched
    at most once per cooldown; anything else is fetched on demand."""
    if stale or (kind, clat, clon) in _failed:
        return _retry_due(kind, clat, clon)
    return True


def _note_failure(kind, clat, clon):
    """A failed fetch starts the cooldown, however the fetch was triggered."""
    now = time.time()
    _failed[(kind, clat, clon)] = now
    _retry[(kind, clat, clon)] = now

API = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
       "&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=kn"
       "&past_days={past}&forecast_days=7&timeformat=unixtime")
PAST_DAYS = 7          # the normal series window behind now
ARCHIVE_DAYS = 92      # the most the API will reach back: replays older than
                       # this sail placeholder wind, recorded as such


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
        stale = any(r["source"] == "synthetic" for r in have.values())
        missing = hour not in have or (hour + 3600) not in have
        if (missing or stale) and _fetch_allowed("wind", clat, clon, stale):
            _fetch_cell(db, clat, clon, for_hour=hour)
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


def _http_json(url, attempts=3, timeout=15):
    """GET with a few short-backoff retries — one blip must not poison a cell."""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(1 + 2 * i)


def _download_wind(clat, clon, attempts=3, past_days=PAST_DAYS):
    """The hourly series for one grid cell from the API, as cache rows."""
    data = _http_json(API.format(lat=clat, lon=clon, past=past_days), attempts=attempts)
    hh = data["hourly"]
    rows = []
    for t, spd, deg in zip(hh["time"], hh["wind_speed_10m"], hh["wind_direction_10m"]):
        if spd is None or deg is None:
            continue
        rows.append((clat, clon, int(t), float(deg), float(spd), "open-meteo"))
    if not rows:
        raise ValueError("empty wind series")
    return rows


def _store_wind(db, clat, clon, rows):
    """Real data in; whatever placeholder was standing in for it goes out."""
    db.executemany(
        "INSERT OR REPLACE INTO wind_cache(lat,lon,t,twd,tws,source) "
        "VALUES (?,?,?,?,?,?)", rows)
    db.execute("DELETE FROM wind_cache WHERE lat=? AND lon=? AND source='synthetic'",
               (clat, clon))
    db.commit()
    _failed.pop(("wind", clat, clon), None)


def _store_placeholder_wind(db, clat, clon, hours=None):
    """Synthetic wind for the given hours (default: a short window around
    now), so the boat keeps sailing while real data is unavailable. Never
    overwrites real rows."""
    if hours is None:
        now = int(time.time())
        hours = [(now // 3600 + h) * 3600 for h in range(*FAIL_FILL_HOURS)]
    rows = []
    for t in hours:
        twd, tws = _synthetic(clat, clon, t)
        rows.append((clat, clon, t, twd, tws, "synthetic"))
    db.executemany(
        "INSERT OR IGNORE INTO wind_cache(lat,lon,t,twd,tws,source) "
        "VALUES (?,?,?,?,?,?)", rows)
    db.commit()


def _fetch_cell(db, clat, clon, for_hour=None):
    """Fetch one grid cell into the cache. An hour older than the normal
    series window (a replay, a restart, a postponed gun) widens the request
    back to the API's archive limit. On failure a short window is filled
    with placeholder wind and the cell is left for the cooldown retry; an
    hour the archive cannot reach gets placeholder rows too, gated the
    same way, so a replay never hammers the API step after step."""
    past_days = PAST_DAYS
    if for_hour is not None:
        back = int(math.ceil((time.time() - for_hour) / 86400.0)) + 1
        past_days = max(PAST_DAYS, min(ARCHIVE_DAYS, back))
    try:
        rows = _download_wind(clat, clon, past_days=past_days)
    except Exception:
        _note_failure("wind", clat, clon)
        _store_placeholder_wind(db, clat, clon)
        return False
    _store_wind(db, clat, clon, rows)
    if for_hour is not None and not any(r[2] == for_hour for r in rows):
        _note_failure("wind", clat, clon)
        _store_placeholder_wind(db, clat, clon, hours=[for_hour, for_hour + 3600])
        return False
    return True


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
        stale = any(r["source"] == "none" for r in have.values())
        missing = hour not in have or (hour + 3600) not in have
        if (missing or stale) and _fetch_allowed("current", clat, clon, stale):
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


def _download_current(clat, clon, attempts=3):
    data = _http_json(MARINE_API.format(lat=clat, lon=clon), attempts=attempts)
    hh = data["hourly"]
    rows = []
    for t, spd, deg in zip(hh["time"], hh["ocean_current_velocity"],
                           hh["ocean_current_direction"]):
        rows.append((clat, clon, int(t),
                     float(deg or 0.0), float(spd or 0.0) * KMH_TO_KN,
                     "open-meteo"))
    if not rows:
        raise ValueError("empty current series")
    return rows


def _store_current(db, clat, clon, rows):
    db.executemany(
        "INSERT OR REPLACE INTO current_cache(lat,lon,t,cdir,cspd,source) "
        "VALUES (?,?,?,?,?,?)", rows)
    db.execute("DELETE FROM current_cache WHERE lat=? AND lon=? AND source='none'",
               (clat, clon))
    db.commit()
    _failed.pop(("current", clat, clon), None)


def _fetch_current_cell(db, clat, clon):
    try:
        rows = _download_current(clat, clon)
    except Exception:
        # no marine data here (land cell / offline): zero current for a short
        # window, cached so we don't retry every step; healed on the cooldown
        _note_failure("current", clat, clon)
        now = int(time.time())
        rows = [(clat, clon, (now // 3600 + h) * 3600, 0.0, 0.0, "none")
                for h in range(*FAIL_FILL_HOURS)]
        db.executemany(
            "INSERT OR IGNORE INTO current_cache(lat,lon,t,cdir,cspd,source) "
            "VALUES (?,?,?,?,?,?)", rows)
        db.commit()
        return False
    _store_current(db, clat, clon, rows)
    return True


def heal_fallback(db, now=None, limit=5):
    """Refetch cells that hold placeholder data in the live window.

    Called from the ticker every minute. Each cell is attempted at most once
    per RETRY_SECONDS (shared with the on-access retry), and at most `limit`
    cells per call so one tick never spends long on a dead API. Network
    work happens outside the cache lock. Returns the number of cells healed.
    """
    now = int(now or time.time())
    lo, hi = now + LIVE_WINDOW[0], now + LIVE_WINDOW[1]
    healed = 0
    for kind, table, src, download, store in (
            ("wind", "wind_cache", "synthetic", _download_wind, _store_wind),
            ("current", "current_cache", "none", _download_current, _store_current)):
        cells = db.execute(
            f"SELECT DISTINCT lat, lon FROM {table} WHERE source=? AND t BETWEEN ? AND ?",
            (src, lo, hi)).fetchall()
        for c in cells:
            if healed >= limit:
                return healed
            clat, clon = c["lat"], c["lon"]
            if not _retry_due(kind, clat, clon):
                continue
            try:
                rows = download(clat, clon, attempts=1)
            except Exception:
                _note_failure(kind, clat, clon)
                continue
            with _lock:
                store(db, clat, clon, rows)
            healed += 1
    return healed


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


def wind_health(db, now=None, bbox=None):
    """Is the fleet sailing real weather right now?

    Degraded means synthetic wind sits in the cache in the window a routing
    would be evaluated against (recent past through the next day). With a
    bbox (lat0, lat1, lon0, lon1) only cells inside it count, so one race's
    outage never pauses another race's uploads. heal_fallback() normally
    clears it within a cooldown or two; while it lasts the race page shows
    a warning and routing uploads for that race are paused.
    """
    now = int(now or time.time())
    q = ("SELECT COUNT(*) c, COUNT(DISTINCT lat || ',' || lon) cells, MIN(t) t0 "
         "FROM wind_cache WHERE source='synthetic' AND t BETWEEN ? AND ?")
    args = [now + LIVE_WINDOW[0], now + LIVE_WINDOW[1]]
    if bbox:
        q += " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        args += [bbox[0], bbox[1], bbox[2], bbox[3]]
    r = db.execute(q, args).fetchone()
    return {"degraded": r["c"] > 0, "synthetic_cells": r["cells"],
            "since": r["t0"] if r["c"] else None}
