"""Real-vs-virtual performance decomposition.

Why is a tracked boat ahead of (or behind) a virtual boat sailing the race
polar?  One leaderboard gap hides two very different answers:

  * **boat speed** — the real boat is faster or slower than the race polar
    says.  Crew, sails, sea state, and a polar that isn't really the boat's
    all land here.  It is measured on the real boat's *own* track: for every
    fix-to-fix segment, with the wind the model holds for that place and
    time, how long would a polar boat have needed to cover the same ground?
    That ratio is the "% of polar" navigators read off Expedition or Adrena.

  * **navigation** — where each boat chose to go.  Once the real boat's
    track has been re-sailed at polar speed, what is left of the gap is two
    polar boats on two routes: more wind found, fewer miles sailed, fewer
    tacks paid for.

  * **start** — a virtual boat that entered after the gun owes the offset.

The three sum exactly to the time gap at equal progress along the course
(the leaderboard's distance-to-finish), so nothing hides in the rounding.
"""
import math
import time

from .geo import angle_diff, bearing_deg, haversine_nm
from .sim import dtf_nm, race_polar
from .wind import _cell, _lerp_angle, get_current, get_wind

FETCH_WINDOW_S = 6 * 86400   # the forecast API serves ±7 d; beyond, cache only
SPEED_FLOOR_KN = 0.5         # a polar boat never needs infinite time for a leg
POS_BANDS = [("upwind", 0, 60), ("reaching", 60, 120), ("downwind", 120, 181)]
TWS_BANDS = [("under 8 kn", 0, 8), ("8–14 kn", 8, 14),
             ("14–20 kn", 14, 20), ("over 20 kn", 20, 999)]


class CompareError(ValueError):
    """The comparison cannot be made yet (nobody has left the line, etc.)."""


# ---- weather lookups that never poison the cache ----------------------------

def _cached(db, table, cols, lat, lon, t):
    clat, clon = _cell(lat, lon)
    hour = int(t // 3600) * 3600
    rows = db.execute(
        f"SELECT t, {cols[0]} a, {cols[1]} b, source FROM {table} "
        "WHERE lat=? AND lon=? AND t IN (?, ?)",
        (clat, clon, hour, hour + 3600)).fetchall()
    have = {r["t"]: r for r in rows if r["source"] not in ("synthetic", "none")}
    a, b = have.get(hour), have.get(hour + 3600)
    if a and b:
        f = (t - hour) / 3600.0
        return _lerp_angle(a["a"], b["a"], f), a["b"] + f * (b["b"] - a["b"])
    if a or b:
        r = a or b
        return r["a"], r["b"]
    return None


def wind_at(db, lat, lon, t, now):
    """(twd, tws) or None.  Times the forecast API can still serve go through
    the normal provider, filling the cache exactly as the sim does; older
    times read the cache only — a miss there must neither refetch on every
    segment nor be papered over with placeholder wind."""
    if abs(t - now) <= FETCH_WINDOW_S:
        twd, tws, src = get_wind(db, lat, lon, t)
        return None if src == "synthetic" else (twd, tws)
    return _cached(db, "wind_cache", ("twd", "tws"), lat, lon, t)


def current_at(db, lat, lon, t, now):
    if abs(t - now) <= FETCH_WINDOW_S:
        cdir, cspd, _ = get_current(db, lat, lon, t)
        return cdir, cspd
    return _cached(db, "current_cache", ("cdir", "cspd"), lat, lon, t) or (0.0, 0.0)


# ---- progress along the course ---------------------------------------------

def progress(points, marks, radius_nm):
    """Walk fixes [(t, lat, lon), ...] through the course with the same
    mark-passing rule as the sim and the tracker ingester.
    Returns [(t, lat, lon, dtf_nm, next_mark)]."""
    out = []
    nm = 1
    for (t, la, lo) in points:
        while nm < len(marks) and haversine_nm(
                la, lo, marks[nm]["lat"], marks[nm]["lon"]) <= radius_nm:
            nm += 1
        out.append((t, la, lo, dtf_nm(la, lo, marks, nm), nm))
    return out


def time_at(prog, target_dtf):
    """First moment the boat's distance-to-finish drops to `target_dtf`,
    interpolated between fixes; None if it never has."""
    for i, p in enumerate(prog):
        if p[3] <= target_dtf + 1e-9:
            if i == 0:
                return p[0]
            q = prog[i - 1]
            span = q[3] - p[3]
            f = (q[3] - target_dtf) / span if span > 1e-9 else 1.0
            return q[0] + f * (p[0] - q[0])
    return None


def _interp_fix(a, b, t):
    f = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
    return (t, a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]))


def clip(pts, t0, t1):
    """The part of a fix list between t0 and t1, endpoints interpolated."""
    out = []
    for i, p in enumerate(pts):
        if p[0] < t0:
            if i + 1 < len(pts) and pts[i + 1][0] > t0:
                out.append(_interp_fix(p, pts[i + 1], t0))
            continue
        if p[0] > t1:
            if i > 0 and pts[i - 1][0] < t1:
                out.append(_interp_fix(pts[i - 1], p, t1))
            break
        out.append(tuple(p[:3]))
    return out


# ---- re-sailing a track at polar speed ---------------------------------------

def resail(db, race, polar, fixes, now):
    """Re-sail a ground track at race-polar speed through the model's wind.

    For each segment the polar boat steers the sim's own rule — best speed
    made good along the segment's bearing (so a dead-upwind segment costs
    tacking VMG, never a parked boat) — plus the surface current's push
    along the track when the race sails currents.  Returns the actual and
    polar hours, coverage of the wind model over the track, and % of polar
    split by point of sail and wind strength."""
    perf = race["perf_factor"]
    use_cur = bool(race["currents_enabled"])
    dist = hours = covered = polar_h = tws_h = 0.0
    pos = {k: [0.0, 0.0] for k, _, _ in POS_BANDS}    # band -> [actual h, polar h]
    tws_b = {k: [0.0, 0.0] for k, _, _ in TWS_BANDS}
    for a, b in zip(fixes, fixes[1:]):
        dt = (b[0] - a[0]) / 3600.0
        if dt <= 0:
            continue
        d = haversine_nm(a[1], a[2], b[1], b[2])
        dist += d
        hours += dt
        mlat, mlon, mt = (a[1] + b[1]) / 2, (a[2] + b[2]) / 2, (a[0] + b[0]) / 2
        w = wind_at(db, mlat, mlon, mt, now)
        if w is None:
            continue
        twd, tws = w
        twa = None
        if d > 1e-4:
            brg = bearing_deg(a[1], a[2], b[1], b[2])
            vmc = polar.best_vmc(brg, twd, tws, perf)[0]
            if use_cur:
                cdir, cspd = current_at(db, mlat, mlon, mt, now)
                vmc += cspd * math.cos(math.radians(cdir - brg))
            ph = d / max(vmc, SPEED_FLOOR_KN)
            twa = angle_diff(brg, twd)
        else:
            ph = 0.0                    # no ground covered: a polar boat needs 0 h
        covered += dt
        polar_h += ph
        tws_h += tws * dt
        if twa is not None:
            for k, lo, hi in POS_BANDS:
                if lo <= twa < hi:
                    pos[k][0] += dt
                    pos[k][1] += ph
                    break
        for k, lo, hi in TWS_BANDS:
            if lo <= tws < hi:
                tws_b[k][0] += dt
                tws_b[k][1] += ph
                break

    def bands(spec, acc):
        return [{"band": k,
                 "share": acc[k][0] / covered if covered else None,
                 "pct_polar": 100.0 * acc[k][1] / acc[k][0]
                 if acc[k][0] > 0.05 else None}
                for k, _, _ in spec]

    cov = covered / hours if hours else 0.0
    return {
        "distance_nm": dist, "hours": hours,
        "avg_sog": dist / hours if hours else None,
        "coverage": cov,
        "avg_tws": tws_h / covered if covered else None,
        # the uncovered remainder is assumed to sail at the same % of polar
        "polar_hours": polar_h / cov if cov > 0 else None,
        "pct_polar": 100.0 * polar_h / covered if covered else None,
        "by_pos": bands(POS_BANDS, pos),
        "by_tws": bands(TWS_BANDS, tws_b),
    }


# ---- the decomposition ------------------------------------------------------------

def _fmt_h(h):
    m = int(round(abs(h) * 60))
    return f"{m // 60} h {m % 60:02d} min"


def compare(db, race, marks, rb, boat=None, now=None):
    """Decompose the gap between real boat `rb` and virtual `boat` (rows).

    Without a virtual boat only the real boat's polar report is produced.
    Raises CompareError when a boat has no sailed track yet."""
    now = int(now or time.time())
    polar = race_polar(race)
    radius = race["mark_radius_nm"]
    notes = []

    rpts = [(r["t"], r["lat"], r["lon"]) for r in db.execute(
        "SELECT t,lat,lon FROM real_track WHERE rb_id=? ORDER BY t", (rb["id"],))]
    if not rpts or rpts[-1][0] <= race["start_time"]:
        raise CompareError(f"{rb['name']} has no tracker fixes since the gun")
    # the real boat's clock starts at the gun, or at its first fix after it
    t0_r = max(race["start_time"], rpts[0][0])
    if rpts[0][0] > race["start_time"] + 900:
        notes.append(f"{rb['name']}'s tracker history begins "
                     f"{_fmt_h((rpts[0][0] - race['start_time']) / 3600)} after "
                     "the gun; its clock starts there")
    rpts = clip(rpts, t0_r, rpts[-1][0])
    rprog = progress(rpts, marks, radius)
    r_fin = rprog[-1][4] >= len(marks)
    dtf_r_now = 0.0 if r_fin else rprog[-1][3]

    out = {"perf_factor": race["perf_factor"], "polar_name": race["polar_name"],
           "notes": notes, "virtual": None, "components": None, "gap_h": None}

    if boat is None:
        # polar report only: the whole track so far, or up to the finish
        target = dtf_r_now
        t_r = time_at(rprog, 0.0) if r_fin else rpts[-1][0]
        out["real"] = _boat_block(db, race, polar, rb["id"], rb["name"],
                                  rb["klass"] or "", rpts, t0_r, t_r, now, notes)
        out["target_dtf_nm"] = target
        out["reference"] = "the finish" if r_fin else f"{round(target)} nm to go"
        return out

    if boat["sim_time"] is None:
        raise CompareError(f"{boat['name']} has not left the line yet")
    step = race["step_minutes"] * 60
    vrows = db.execute("SELECT t,lat,lon FROM track WHERE boat_id=? ORDER BY t",
                       (boat["id"],)).fetchall()
    if not vrows:
        raise CompareError(f"{boat['name']} has not left the line yet")
    t0_v = vrows[0]["t"] - step
    vpts = [(t0_v, marks[0]["lat"], marks[0]["lon"])] + \
           [(r["t"], r["lat"], r["lon"]) for r in vrows]
    vprog = progress(vpts, marks, radius)
    v_fin = vprog[-1][4] >= len(marks)
    dtf_v_now = 0.0 if v_fin else vprog[-1][3]

    # compare at the progress of whoever is behind — the same yardstick the
    # leaderboard ranks by
    target = max(dtf_r_now, dtf_v_now)
    t_r = time_at(rprog, target)
    t_v = time_at(vprog, target)
    if t_r is None or t_v is None:      # cannot happen: the trailer defines target
        raise CompareError("boats have no common progress point yet")

    real = _boat_block(db, race, polar, rb["id"], rb["name"], rb["klass"] or "",
                       rpts, t0_r, t_r, now, notes)
    virt = _boat_block(db, race, polar, boat["id"], boat["name"], "virtual",
                       vpts, t0_v, t_v, now, notes)
    virt["maneuvers"] = boat["maneuvers"] or 0

    gap_h = (t_v - t_r) / 3600.0          # + : the virtual boat is behind
    start_h = (t0_v - t0_r) / 3600.0
    comps = None
    if real["polar_hours"] is not None:
        boat_speed_h = real["polar_hours"] - real["elapsed_h"]
        comps = {"boat_speed_h": boat_speed_h,
                 "navigation_h": gap_h - start_h - boat_speed_h,
                 "start_h": start_h}
    else:
        notes.append("the gap cannot be split without wind along "
                     f"{rb['name']}'s track")
    if abs(start_h) > 1 / 60:
        notes.append(f"{boat['name']} started {_fmt_h(start_h)} "
                     f"{'after' if start_h > 0 else 'before'} {rb['name']}'s clock")

    out.update({"real": real, "virtual": virt, "target_dtf_nm": target,
                "reference": "the finish" if target <= 1e-9 else f"{round(target)} nm to go",
                "gap_h": gap_h, "components": comps})
    return out


def _boat_block(db, race, polar, bid, name, klass, pts, t0, t1, now, notes):
    stats = resail(db, race, polar, clip(pts, t0, t1), now)
    if stats["hours"] > 0 and stats["coverage"] == 0:
        notes.append(f"no wind data along {name}'s track — the model only "
                     "holds weather fetched while the race was live, so it "
                     "cannot be re-sailed at polar speed")
    elif 0 < stats["coverage"] < 0.98:
        notes.append(f"wind data covers {round(stats['coverage'] * 100)}% of "
                     f"{name}'s track; the rest is assumed to sail at the same "
                     "% of polar")
    stats.update({"id": bid, "name": name, "klass": klass,
                  "t0": t0, "t_at": t1, "elapsed_h": (t1 - t0) / 3600.0})
    return stats
