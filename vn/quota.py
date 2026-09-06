"""How many Open-Meteo calls this process has made, for /healthz.

Open-Meteo's free tier is for non-commercial use and allows 10,000 calls a
day, 5,000 an hour and 600 a minute; a request for several locations or a
long time range counts as more than one call on their side, so this
counter is a floor, not their bill. Kinds: "wind" and "current" (one 0.25°
cell each, vn/wind.py), "forecast" (one per grid point in a snapshot batch,
vn/forecast.py). Counts live in memory: the ticker and /healthz share the
one gunicorn worker, and a restart resets them, which is fine for a gauge.
A warning is logged once an hour while the day's count is past WARN_SHARE
of the limit.
"""
import collections
import logging
import threading
import time

log = logging.getLogger("vn.quota")

DAY_LIMIT = 10_000
HOUR_LIMIT = 5_000
WARN_SHARE = 0.8

_lock = threading.Lock()
_calls = collections.deque()          # (unix, kind, n) for the last 24 h
_last_warn = 0.0


def note_calls(kind, n=1, now=None):
    """Record n calls of a kind and prune anything older than a day."""
    global _last_warn
    now = now or time.time()
    with _lock:
        _calls.append((now, kind, n))
        cutoff = now - 86400
        while _calls and _calls[0][0] < cutoff:
            _calls.popleft()
        day = sum(c[2] for c in _calls)
    if day >= DAY_LIMIT * WARN_SHARE and now - _last_warn > 3600:
        _last_warn = now
        log.warning("Open-Meteo: %d calls in the last 24 h (free tier allows %d)", day, DAY_LIMIT)


def summary(now=None):
    now = now or time.time()
    with _lock:
        day = [c for c in _calls if c[0] >= now - 86400]
        hour = [c for c in day if c[0] >= now - 3600]
        by_kind = collections.Counter()
        for _, kind, n in day:
            by_kind[kind] += n
    return {"calls_24h": sum(c[2] for c in day), "calls_1h": sum(c[2] for c in hour),
            "by_kind_24h": dict(by_kind),
            "limit_day": DAY_LIMIT, "limit_hour": HOUR_LIMIT}


def reset():
    """Tests."""
    global _last_warn
    with _lock:
        _calls.clear()
    _last_warn = 0.0
