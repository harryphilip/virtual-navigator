"""Tracked real-boat state.

Tracker positions arrive in batches (YB poller, CSV/GPX imports).  Instead of
re-walking each boat's whole track on every leaderboard request, mark
progression / finish / SOG are folded into the real_boats row as points are
ingested.  Points stamped in the future are dropped — the leaderboard only
ever knows what a tracker could have broadcast by now.
"""
import time

from .geo import haversine_nm


def ingest_points(db, race, marks, rb_id, pts, now=None):
    """Insert tracker points [(t, lat, lon), ...] and update boat state.

    Returns the number of genuinely new points stored.
    """
    now = int(now or time.time())
    pts = sorted(p for p in pts if p[0] <= now)
    if not pts:
        return 0
    rb = db.execute("SELECT * FROM real_boats WHERE id=?", (rb_id,)).fetchone()
    cur = db.executemany(
        "INSERT OR IGNORE INTO real_track(rb_id,t,lat,lon) VALUES (?,?,?,?)",
        [(rb_id, t, la, lo) for (t, la, lo) in pts])
    added = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    if rb["last_t"] is not None and pts[0][0] <= rb["last_t"]:
        recompute(db, race, marks, rb_id)          # backfill: rebuild state
    else:
        _walk(db, race, marks, rb, pts)
    db.commit()
    return added


def recompute(db, race, marks, rb_id):
    """Rebuild stored state from the full track (used after backfills)."""
    rb = db.execute("SELECT * FROM real_boats WHERE id=?", (rb_id,)).fetchone()
    db.execute("UPDATE real_boats SET last_t=NULL, last_lat=NULL, last_lon=NULL,"
               " sog=NULL, next_mark=1, finished_at=NULL WHERE id=?", (rb_id,))
    rb = db.execute("SELECT * FROM real_boats WHERE id=?", (rb_id,)).fetchone()
    pts = [(r["t"], r["lat"], r["lon"]) for r in db.execute(
        "SELECT t,lat,lon FROM real_track WHERE rb_id=? ORDER BY t", (rb_id,))]
    _walk(db, race, marks, rb, pts)


def _walk(db, race, marks, rb, pts):
    next_mark = rb["next_mark"] or 1
    finished = rb["finished_at"]
    prev = (rb["last_t"], rb["last_lat"], rb["last_lon"])
    sog = rb["sog"]
    for (t, la, lo) in pts:
        while next_mark < len(marks) and haversine_nm(
                la, lo, marks[next_mark]["lat"], marks[next_mark]["lon"]
        ) <= race["mark_radius_nm"]:
            next_mark += 1
            if next_mark >= len(marks) and finished is None:
                finished = t
        if prev[0] is not None and t > prev[0]:
            dt_h = (t - prev[0]) / 3600.0
            if dt_h > 0.02:
                sog = haversine_nm(prev[1], prev[2], la, lo) / dt_h
        prev = (t, la, lo)
    db.execute(
        "UPDATE real_boats SET last_t=?, last_lat=?, last_lon=?, sog=?, "
        "next_mark=?, finished_at=? WHERE id=?",
        (prev[0], prev[1], prev[2], sog, next_mark, finished, rb["id"]))
