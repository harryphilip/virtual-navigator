"""Race simulation engine.

Every virtual boat sails the race polar (scaled by the race performance
factor) through the same weather.  Boats follow their submitted routing
waypoints; the engine steers whatever heading gives the best speed made good
along the rhumb line to the next waypoint (so a waypoint dead upwind is still
reachable, at realistic VMG cost).  Time only moves forward: each boat's state
is advanced lazily to "now", written to an immutable track, and route updates
can only replace waypoints not yet reached.
"""
import json
import math
import threading
import time

from .depth import get_depth_ft
from .geo import angle_diff, haversine_nm, bearing_deg, destination, point_in_poly
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
        zones = race_zones(race)
        boats = db.execute(
            "SELECT * FROM boats WHERE race_id=? AND sim_time IS NOT NULL",
            (race_id,)).fetchall()
        for boat in boats:
            _advance(db, race, polar, marks, zones, boat, now)
        db.commit()


def race_zones(race):
    """Exclusion zone polygons for a race row: [{'name', 'pts'}]."""
    try:
        return json.loads(race["zones_json"] or "[]")
    except (KeyError, IndexError, ValueError):
        return []


def _advance(db, race, polar, marks, zones, boat, until):
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
    zone_steps = boat["zone_steps"] or 0
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
        path = [(lat, lon)]          # every position the boat occupies this step
        twd, tws, _src = get_wind(db, lat, lon, t)
        hours = step / 3600.0

        # grounding: in less water than the race minimum the boat drags
        # through at half speed for the step — slow, never disqualified
        aground = min_depth_ft > 0 and get_depth_ft(db, lat, lon) < min_depth_ft
        # exclusion zones (TSS, ice, wildlife) cost the same drag: the SIs
        # say keep out, the game makes inside slower than around
        in_zone = any(point_in_poly(lat, lon, z["pts"]) for z in zones)
        speed_scale = 0.5 if (aground or in_zone) else 1.0
        if aground:
            groundings += 1
        if in_zone:
            zone_steps += 1

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
                path.append((lat, lon))
        else:
            hdg = twa = None
            bsp = 0.0   # no routing left: boat parks and waits for orders

        # surface current sets the boat for the whole step, sailing or parked
        if use_current:
            cdir, cspd, _csrc = get_current(db, lat, lon, t)
            if cspd > 0.01:
                lat, lon = destination(lat, lon, cdir, cspd * hours)
        path.append((lat, lon))

        # course-mark / finish handling: a mark is passed when the boat's
        # path this step comes within the mark radius of it — judged along
        # the whole path, not just where the step ends, because a routing
        # that passes a mark abeam crosses the radius on a chord that can
        # be shorter than one step
        while next_mark < len(marks):
            i = _path_reaches(marks[next_mark], path, race["mark_radius_nm"])
            if i is None:
                break
            next_mark += 1
            path = path[i:]          # later marks count only from here on
            if next_mark >= len(marks):
                finished = t2
                break

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
        "wind_side=?, maneuvers=?, groundings=?, zone_steps=? WHERE id=?",
        (t, lat, lon, next_mark, finished, side, maneuvers, groundings,
         zone_steps, boat["id"]))


MARK_SIDES = ("port", "stbd")
SIDE_WORD = {"port": "port", "stbd": "starboard"}


def mark_side(mk):
    """'port' | 'stbd' | None — the side a boat must leave the mark on."""
    try:
        side = mk["side"]
    except (KeyError, IndexError):
        return None
    return side if side in MARK_SIDES else None


def _signed_turn(a, b):
    """Signed change from bearing a to bearing b, in (-180, 180]."""
    return ((b - a + 180.0) % 360.0) - 180.0


def _seg_dist_nm(mk, a, b):
    """Closest approach of the straight leg a→b to the mark, in nm (flat earth)."""
    k = math.cos(math.radians(mk["lat"])) * 60.0
    ax, ay = (a[1] - mk["lon"]) * k, (a[0] - mk["lat"]) * 60.0
    bx, by = (b[1] - mk["lon"]) * k, (b[0] - mk["lat"]) * 60.0
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy)


def _path_reaches(mk, path, radius_nm):
    """Index of the first segment of `path` that comes within radius_nm of
    the mark, or None. A one-point path is judged as a point."""
    if len(path) == 1:
        return 0 if haversine_nm(path[0][0], path[0][1], mk["lat"], mk["lon"]) <= radius_nm else None
    for i in range(len(path) - 1):
        if _seg_dist_nm(mk, path[i], path[i + 1]) <= radius_nm:
            return i
    return None


def _required_sweep(mk, a, b, side):
    """Taut-string sweep from a round the mark to b in the required direction.

    Returns (bearing of a from the mark, sweep in degrees, +1/-1 direction).
    Legs that come in and go out within 90° of each other make a turning
    mark: there the side means a full rounding, not a touch-and-go past it.
    """
    ba = bearing_deg(mk["lat"], mk["lon"], a[0], a[1])
    bb = bearing_deg(mk["lat"], mk["lon"], b[0], b[1])
    sgn = 1.0 if side == "stbd" else -1.0
    sweep = (sgn * (bb - ba)) % 360.0
    if sweep < 90.0:
        sweep += 360.0
    return ba, sweep, sgn


def _rounding_arc(mk, a, b, side, off_nm, step_deg=45.0):
    """Waypoints that take a boat from a round the mark to b, leaving it to `side`.

    Points sit off_nm from the mark, swept from a's bearing to b's bearing —
    clockwise for starboard, anticlockwise for port — through the sweep the
    taut-string rule requires (a full circle at a turning mark).
    """
    ba, sweep, sgn = _required_sweep(mk, a, b, side)
    n = max(1, int(math.ceil(sweep / step_deg)))
    return [destination(mk["lat"], mk["lon"], (ba + sgn * sweep * i / n) % 360.0, off_nm)
            for i in range(n + 1)]


def _leg_sweep(mk, leg):
    """Net signed bearing sweep (mark → boat) along a leg: + clockwise, − anti.

    A pass-by on the correct side sweeps ~±180°, a rounding ~±360°, and a
    touch-and-go ~0°; the sign says which side the mark was left on.  Also
    reports whether any leg runs straight over the mark, where the sign is
    meaningless.  Compared against _required_sweep with 90° of slack.
    """
    total, over = 0.0, False
    for i in range(len(leg) - 1):
        total += _signed_turn(bearing_deg(mk["lat"], mk["lon"], leg[i][0], leg[i][1]),
                              bearing_deg(mk["lat"], mk["lon"], leg[i + 1][0], leg[i + 1][1]))
        if _seg_dist_nm(mk, leg[i], leg[i + 1]) < 0.05:
            over = True
    return total, over


def enforce_course(wps, marks, next_mark, radius_nm, start_pos=None, cog=None):
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
        nothing to be thrown out for,
      * for marks with a required side ('port' / 'stbd'), checks which way
        the routing actually passes — the net sweep of the mark's bearing
        along the leg — and where it goes the wrong way, touches and turns
        back, or runs straight over the mark, replaces that pass with a
        rounding half a mark-radius off the mark on the correct side.  An
        inserted rounding for a missed sided mark is built the same way.

    Returns (waypoints, notes).
    """
    wps = list(wps)
    notes = []
    if start_pos is not None and wps:
        # A mid-race re-submission often re-includes the already-sailed part
        # (a full re-run of the router from the start).  Join the new routing
        # at its closest point to the boat — searching only the portion of
        # the routing before it first reaches the next course mark, so an
        # out-and-back course can never tempt the join toward the finish.
        j = len(wps)
        if next_mark < len(marks):
            mk = marks[next_mark]
            # skip any initial dwell at the mark itself first — on an
            # out-and-back course the routing's first waypoint can sit on
            # the finish, which is also the next mark
            s = 0
            while s < len(wps) and haversine_nm(
                    wps[s][0], wps[s][1], mk["lat"], mk["lon"]) <= radius_nm:
                s += 1
            for i in range(s, len(wps)):
                if haversine_nm(wps[i][0], wps[i][1],
                                mk["lat"], mk["lon"]) <= radius_nm:
                    j = i + 1
                    break
        def _leg_dir(i):
            if i + 1 < len(wps):
                return bearing_deg(wps[i][0], wps[i][1], wps[i + 1][0], wps[i + 1][1])
            if i > 0:
                return bearing_deg(wps[i - 1][0], wps[i - 1][1], wps[i][0], wps[i][1])
            return None

        def _score(i):
            d = haversine_nm(start_pos[0], start_pos[1], wps[i][0], wps[i][1])
            # a leg pointing against the boat's course over ground is the
            # wrong pass of an out-and-back — push it far down the ranking
            ld = _leg_dir(i)
            if cog is not None and ld is not None and angle_diff(ld, cog) > 100:
                d += 25.0
            return d

        k = min(range(j), key=_score)
        # walk forward past every segment the boat has already overtaken —
        # joins ahead, never astern
        while k + 1 < len(wps) and haversine_nm(
                start_pos[0], start_pos[1], wps[k + 1][0], wps[k + 1][1]
        ) <= haversine_nm(wps[k][0], wps[k][1], wps[k + 1][0], wps[k + 1][1]):
            k += 1
        if k > 0:
            wps = wps[k:]
            notes.append(f"joined the new routing at its closest point to "
                         f"your position — dropped {k} already-passed "
                         "waypoint(s) behind you")
        dropped = 0
        while wps and haversine_nm(start_pos[0], start_pos[1],
                                   wps[0][0], wps[0][1]) <= radius_nm:
            wps.pop(0)
            dropped += 1
        if dropped and not k:
            notes.append(f"skipped {dropped} leading waypoint(s) already at "
                         "your position")
    off_nm = max(0.1, min(1.0, 0.5 * radius_nm))   # rounding distance off a sided mark
    pos = 0
    for k, mk in enumerate(marks[next_mark:], start=next_mark):
        side = mark_side(mk)
        leg_start = pos
        best_i, best_d = None, float("inf")
        first_in = None
        for i in range(pos, len(wps)):
            d = haversine_nm(mk["lat"], mk["lon"], wps[i][0], wps[i][1])
            if d < best_d:
                best_i, best_d = i, d
            if d <= radius_nm:
                first_in = i
                break
        if first_in is None:
            insert_at = best_i + 1 if best_i is not None else len(wps)
            if side and best_i is not None and best_i + 1 < len(wps):
                arc = _rounding_arc(mk, wps[best_i], wps[best_i + 1], side, off_nm)
                wps[insert_at:insert_at] = arc
                notes.append(f"routing misses {mk['name']} by {best_d:.1f} nm "
                             f"— a rounding leaving it to {SIDE_WORD[side]} "
                             "was inserted")
                pos = insert_at + len(arc)
                continue
            wps.insert(insert_at, (mk["lat"], mk["lon"]))
            if best_d < float("inf"):
                notes.append(f"routing misses {mk['name']} by {best_d:.1f} nm "
                             "— a rounding waypoint was inserted")
            else:
                notes.append(f"routing does not reach {mk['name']} — it was "
                             "appended to your route")
            pos = insert_at + 1
            continue
        pos = first_in + 1
        if not side:
            continue
        # the run of waypoints inside the mark radius, and the leg's
        # approach (a) and departure (b) points outside it
        last_in = first_in
        while last_in + 1 < len(wps) and haversine_nm(
                mk["lat"], mk["lon"], wps[last_in + 1][0], wps[last_in + 1][1]) <= radius_nm:
            last_in += 1
        if last_in + 1 >= len(wps):
            continue                       # route ends at the mark: nothing to judge
        if first_in > 0:
            a = wps[first_in - 1]
        elif start_pos is not None:
            a = tuple(start_pos)
        else:
            continue
        b = wps[last_in + 1]
        # judge the whole leg: from the previous mark up to where the
        # routing first reaches the next one
        end = len(wps)
        if k + 1 < len(marks):
            nxt = marks[k + 1]
            for j in range(last_in + 1, len(wps)):
                if haversine_nm(nxt["lat"], nxt["lon"], wps[j][0], wps[j][1]) <= radius_nm:
                    end = j + 1
                    break
        leg_from = max(0, min(leg_start, first_in - 1))   # always from the approach point
        leg = list(wps[leg_from:end])
        if leg_from == 0 and start_pos is not None:
            leg.insert(0, tuple(start_pos))
        sweep, over = _leg_sweep(mk, leg)
        _, need, sgn = _required_sweep(mk, a, b, side)
        if sgn * sweep >= need - 90.0 and not over:
            continue
        arc = _rounding_arc(mk, a, b, side, off_nm)
        wps[first_in:last_in + 1] = arc
        notes.append(f"routing does not leave {mk['name']} to {SIDE_WORD[side]} "
                     "— that pass was rebuilt as a rounding on the correct side")
        pos = first_in + len(arc)
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


