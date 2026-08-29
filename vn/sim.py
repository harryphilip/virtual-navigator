"""Race simulation engine.

Every virtual boat sails the race polar (scaled by the race performance
factor) through the same weather.  Boats follow their submitted routing
waypoints; the engine steers whatever heading gives the best speed made good
along the rhumb line to the next waypoint (so a waypoint dead upwind is still
reachable, at realistic VMG cost).  Time only moves forward: each boat's state
is advanced lazily to "now", written to an immutable track, and route updates
can only replace waypoints not yet reached.
"""
import threading
import time

from .depth import get_depth_ft
from .geo import haversine_nm, bearing_deg, destination
from .polar import Polar
from .wind import get_wind, get_current

_sim_lock = threading.Lock()
_polar_cache = {}


def race_polar(race):
    key = (race["id"], hash(race["polar_text"]))
    if key not in _polar_cache:
        _polar_cache[key] = Polar.parse(race["polar_text"], race["polar_name"])
    return _polar_cache[key]


def get_marks(db, race_id):
    return db.execute("SELECT * FROM marks WHERE race_id=? ORDER BY seq",
                      (race_id,)).fetchall()


def catch_up_race(db, race_id, now=None):
    """Advance every virtual boat in the race to `now`."""
    now = int(now or time.time())
    with _sim_lock:
        race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
        if race is None:
            return
        polar = race_polar(race)
        marks = get_marks(db, race_id)
        boats = db.execute(
            "SELECT * FROM boats WHERE race_id=? AND sim_time IS NOT NULL",
            (race_id,)).fetchall()
        for boat in boats:
            _advance(db, race, polar, marks, boat, now)
        db.commit()


def _advance(db, race, polar, marks, boat, until):
    if boat["finished_at"] is not None:
        return
    step = race["step_minutes"] * 60
    t = boat["sim_time"]
    if t is None or until <= t:
        return
    lat, lon = boat["lat"], boat["lon"]
    next_mark = boat["next_mark"]
    finished = None
    side = boat["wind_side"]              # +1 wind over starboard, -1 port
    maneuvers = boat["maneuvers"] or 0
    groundings = boat["groundings"] or 0
    penalty_h = (race["maneuver_penalty_s"] or 0.0) / 3600.0
    use_current = bool(race["currents_enabled"])
    min_depth_ft = race["grounding_depth_ft"] or 0.0

    wps = [dict(r) for r in db.execute(
        "SELECT * FROM route_wps WHERE boat_id=? AND passed=0 ORDER BY seq",
        (boat["id"],)).fetchall()]
    passed_wps = []
    points = []

    while t + step <= until and finished is None:
        t2 = t + step
        twd, tws, _src = get_wind(db, lat, lon, t)
        hours = step / 3600.0

        # grounding: in less water than the race minimum the boat drags
        # through at half speed for the step — slow, never disqualified
        aground = min_depth_ft > 0 and get_depth_ft(db, lat, lon) < min_depth_ft
        speed_scale = 0.5 if aground else 1.0
        if aground:
            groundings += 1

        if wps:
            # sail toward the next routing waypoint, possibly reaching
            # several waypoints within one step
            remaining = hours
            hdg = twa = bsp = None
            while remaining > 1e-6 and wps:
                tgt = wps[0]
                brg = bearing_deg(lat, lon, tgt["lat"], tgt["lon"])
                sides = polar.best_vmc_by_side(brg, twd, tws, race["perf_factor"])
                if side in (1, -1):
                    same, other = sides[side], sides[-side]
                    # stay on the current tack unless the other is clearly
                    # better (or this one is dead) — then pay for the maneuver
                    if (other[0] > same[0] * 1.03 and other[0] > 0.05) or \
                       (same[0] <= 0.01 < other[0]):
                        side = -side
                        vmc, hdg, twa, bsp = other
                        maneuvers += 1
                        remaining = max(0.0, remaining - penalty_h)
                    else:
                        vmc, hdg, twa, bsp = same
                else:                      # first step ever: pick freely
                    side = 1 if sides[1][0] >= sides[-1][0] else -1
                    vmc, hdg, twa, bsp = sides[side]
                vmc *= speed_scale
                bsp *= speed_scale
                if vmc <= 0.01 or remaining <= 1e-6:
                    remaining = 0.0
                    break
                d_wp = haversine_nm(lat, lon, tgt["lat"], tgt["lon"])
                d_step = vmc * remaining
                if d_step >= d_wp:
                    lat, lon = tgt["lat"], tgt["lon"]
                    remaining -= d_wp / vmc
                    passed_wps.append(tgt["seq"])
                    wps.pop(0)
                else:
                    lat, lon = destination(lat, lon, brg, d_step)
                    remaining = 0.0
        else:
            hdg = twa = None
            bsp = 0.0   # no routing left: boat parks and waits for orders

        # surface current sets the boat for the whole step, sailing or parked
        if use_current:
            cdir, cspd, _csrc = get_current(db, lat, lon, t)
            if cspd > 0.01:
                lat, lon = destination(lat, lon, cdir, cspd * hours)

        # course-mark / finish handling
        while next_mark < len(marks) and haversine_nm(
                lat, lon, marks[next_mark]["lat"], marks[next_mark]["lon"]
        ) <= race["mark_radius_nm"]:
            next_mark += 1
            if next_mark >= len(marks):
                finished = t2

        points.append((boat["id"], t2, lat, lon, twd, tws, bsp, hdg))
        t = t2

    if points:
        db.executemany(
            "INSERT OR REPLACE INTO track(boat_id,t,lat,lon,twd,tws,bsp,hdg) "
            "VALUES (?,?,?,?,?,?,?,?)", points)
    if passed_wps:
        db.execute(
            "UPDATE route_wps SET passed=1 WHERE boat_id=? AND seq<=?",
            (boat["id"], max(passed_wps)))
    db.execute(
        "UPDATE boats SET sim_time=?, lat=?, lon=?, next_mark=?, finished_at=?, "
        "wind_side=?, maneuvers=?, groundings=? WHERE id=?",
        (t, lat, lon, next_mark, finished, side, maneuvers, groundings, boat["id"]))


def enforce_course(wps, marks, next_mark, radius_nm, start_pos=None):
    """Softly reconcile a submitted routing with the race course.

    A routing exported from navigation software rarely lands exactly on the
    race's marks — start/finish lines are placed slightly differently, and
    roundings may pass just outside the mark radius.  Rather than letting a
    boat park short of the finish (a de-facto DSQ) or sail a shortened
    course (cheating), this:

      * drops leading waypoints the boat is already standing on,
      * walks the remaining course marks in order, and wherever the routing
        never comes within the mark radius of one, inserts the mark itself
        as a waypoint at the routing's closest approach — the boat must
        genuinely sail to every mark, so there is nothing to gain and
        nothing to be thrown out for.

    Returns (waypoints, notes).
    """
    wps = list(wps)
    notes = []
    if start_pos is not None:
        dropped = 0
        while wps and haversine_nm(start_pos[0], start_pos[1],
                                   wps[0][0], wps[0][1]) <= radius_nm:
            wps.pop(0)
            dropped += 1
        if dropped:
            notes.append(f"skipped {dropped} leading waypoint(s) already at "
                         "your position")
    pos = 0
    for mk in marks[next_mark:]:
        best_i, best_d = None, float("inf")
        credited = False
        for i in range(pos, len(wps)):
            d = haversine_nm(mk["lat"], mk["lon"], wps[i][0], wps[i][1])
            if d < best_d:
                best_i, best_d = i, d
            if d <= radius_nm:
                pos = i + 1
                credited = True
                break
        if not credited:
            insert_at = best_i + 1 if best_i is not None else len(wps)
            wps.insert(insert_at, (mk["lat"], mk["lon"]))
            if best_d < float("inf"):
                notes.append(f"routing misses {mk['name']} by {best_d:.1f} nm "
                             "— a rounding waypoint was inserted")
            else:
                notes.append(f"routing does not reach {mk['name']} — it was "
                             "appended to your route")
            pos = insert_at + 1
    return wps, notes


def dtf_nm(lat, lon, marks, next_mark):
    """Distance to finish along the remaining course marks."""
    if lat is None or next_mark >= len(marks):
        return 0.0
    d = haversine_nm(lat, lon, marks[next_mark]["lat"], marks[next_mark]["lon"])
    for i in range(next_mark, len(marks) - 1):
        d += haversine_nm(marks[i]["lat"], marks[i]["lon"],
                          marks[i + 1]["lat"], marks[i + 1]["lon"])
    return d


