"""Audit what a replayed boat actually sailed — READ ONLY, writes nothing.

    .venv/bin/python scripts/audit_replay.py <race_id> <boat name> [since ISO8601]

On Fly:  fly ssh console -C "python /app/scripts/audit_replay.py 3 Magpie"

After a restart the honest question is whether the boat re-sailed the
routing its owner submitted — changed only by the course/zone corrections
the committee owes it — through the weather that was really recorded, with
nothing else moved underneath it.  This answers that:

  * which submitted routing was in force, and how much of it survives
    verbatim (in order) in the armed waypoints;
  * which submitted waypoints were dropped, and which points were inserted
    that the owner never submitted (zone detours, mark roundings, joins);
  * whether any wind the boat sailed through was synthetic (invented
    because a forecast fetch failed) rather than recorded;
  * the race settings that shape a replay, so a change to one of them is
    not mistaken for a polar effect.

`since` (default: the race start) bounds the wind audit to the replayed
window.  Nothing here writes to the database.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db
from vn.geo import haversine_nm
from vn.wind import _cell

SAME_NM = 0.05          # two waypoints this close are the same point


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d %b %H:%M:%SZ")


def _same(a, b):
    return haversine_nm(a[0], a[1], b[0], b[1]) <= SAME_NM


def _length(p):
    return sum(haversine_nm(*p[i], *p[i + 1]) for i in range(len(p) - 1))


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    race_id, name = int(sys.argv[1]), sys.argv[2]

    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    boat = db.execute("SELECT * FROM boats WHERE race_id=? AND name=?",
                      (race_id, name)).fetchone()
    if not (race and boat):
        print("race or boat not found")
        sys.exit(1)
    since = race["start_time"]
    if len(sys.argv) == 4:
        since = int(dt.datetime.fromisoformat(
            sys.argv[3].replace("Z", "+00:00")).timestamp())

    print(f"=== {race['name']} — {name} ===")
    print(f"race start {_iso(race['start_time'])}   "
          f"boat sim_time {_iso(boat['sim_time']) if boat['sim_time'] else None}")

    # ---- 1. settings that shape a replay -----------------------------------
    print("\n--- race settings (anything but the polar changing is a confound) ---")
    for k in ("polar_name", "perf_factor", "step_minutes", "mark_radius_nm",
              "maneuver_penalty_s", "currents_enabled", "grounding_depth_ft"):
        try:
            print(f"  {k:20s} {race[k]}")
        except (KeyError, IndexError):
            pass
    zones = json.loads(race["zones_json"] or "[]")
    marks = db.execute("SELECT * FROM marks WHERE race_id=? ORDER BY seq",
                       (race_id,)).fetchall()
    print(f"  {'marks':20s} {len(marks)}  "
          f"(start {marks[0]['lat']:.4f},{marks[0]['lon']:.4f})" if marks else "")
    print(f"  {'exclusion zones':20s} {len(zones)}")

    # ---- 2. submitted routing vs what is armed -----------------------------
    subs = db.execute(
        "SELECT * FROM route_log WHERE boat_id=? ORDER BY submitted_at",
        (boat["id"],)).fetchall()
    armed = [(r["lat"], r["lon"]) for r in db.execute(
        "SELECT lat,lon FROM route_wps WHERE boat_id=? ORDER BY seq",
        (boat["id"],)).fetchall()]
    print(f"\n--- routing ---")
    print(f"  {len(subs)} submission(s) on file:")
    for s in subs:
        wps = json.loads(s["wp_json"])
        print(f"    {_iso(s['submitted_at'])}  {len(wps):4d} wp  "
              f"{_length([tuple(w) for w in wps]):8.1f} nm")
    if not subs:
        print("    (none — nothing to compare against)")
        return
    sub = [tuple(w) for w in json.loads(subs[-1]["wp_json"])]
    print(f"  latest submission : {len(sub):4d} wp  {_length(sub):8.1f} nm")
    print(f"  armed now         : {len(armed):4d} wp  {_length(armed):8.1f} nm")

    # how much of the submission survives verbatim and in order: walk the
    # armed route, matching each point against the next unmatched submitted
    # waypoint, so inserted points don't stall the comparison
    i = 0
    kept, first_kept = [], None
    for p in armed:
        for k in range(i, len(sub)):
            if _same(p, sub[k]):
                kept.append(k)
                if first_kept is None:
                    first_kept = k
                i = k + 1
                break
    print(f"\n  submitted waypoints still armed, in order: "
          f"{len(kept)}/{len(sub)}")
    # Does the submission even begin at the line?  A routing exported mid-race
    # starts from where the boat was that day, so replaying from the gun has
    # to invent a leg the owner never submitted to reach it.
    if marks:
        line = (marks[0]["lat"], marks[0]["lon"])
        d0 = haversine_nm(sub[0][0], sub[0][1], *line)
        print(f"\n  submission begins at {sub[0][0]:.2f},{sub[0][1]:.2f} — "
              f"{d0:.0f} nm from the start line")
        if d0 > 5:
            print("    ^ the routing does NOT start at the line, so a replay "
                  "from the gun must sail a leg nobody submitted to reach it")
    if first_kept:
        skipped = _length(sub[:first_kept + 1])
        # the line itself is not stored as a waypoint, but the boat sails
        # from it to the first armed point, so that leg counts
        lead = haversine_nm(*line, *armed[0]) if (marks and armed) else 0.0
        head_armed = lead + _length(armed) - _length(sub[first_kept:])
        print(f"  first {first_kept} submitted waypoint(s) dropped — the routing "
              f"is picked up {skipped:.0f} nm along at "
              f"{sub[first_kept][0]:.2f},{sub[first_kept][1]:.2f}")
        # Compare like for like.  The armed head starts at the line, so the
        # honest alternative is sailing to the routing's own first waypoint
        # and following it from there — not the submitted head alone, which
        # begins wherever the router was told to start.
        to_head = haversine_nm(*line, *sub[0]) if marks else 0.0
        alt = to_head + skipped
        print(f"  reaching that point cost {head_armed:.0f} nm as armed, vs "
              f"{alt:.0f} nm sailing to the routing's own head "
              f"({to_head:.0f} nm) and following it ({skipped:.0f} nm)")
        delta = head_armed - alt
        verdict = ("distance-neutral" if abs(delta) < 15 else
                   "SHORTER than the submitted routing — check this is fair"
                   if delta < 0 else "longer than the submitted routing")
        print(f"  => {delta:+.0f} nm — {verdict}")
    inserted = [p for p in armed if not any(_same(p, s) for s in sub)]
    print(f"  points armed that were never submitted: {len(inserted)}")
    for p in inserted[:10]:
        print(f"    {p[0]:.4f},{p[1]:.4f}")
    if len(inserted) > 10:
        print(f"    … and {len(inserted) - 10} more")

    # ---- 3. did the boat sail through invented weather? ---------------------
    trk = db.execute(
        "SELECT t,lat,lon FROM track WHERE boat_id=? AND t>=? ORDER BY t",
        (boat["id"], since)).fetchall()
    print(f"\n--- weather over the replayed window "
          f"({_iso(since)} → now, {len(trk)} fixes) ---")
    if trk:
        # only the cells the boat actually sailed through matter — wind
        # cached elsewhere in the ocean says nothing about this replay
        cells = sorted({_cell(r["lat"], r["lon"]) for r in trk})
        lo, hi = trk[0]["t"], trk[-1]["t"]
        counts, marks_ = {}, ",".join("(?,?)" for _ in cells)
        params = [v for c in cells for v in c] + [lo - 7200, hi + 7200]
        for r in db.execute(
                f"SELECT source, COUNT(*) n FROM wind_cache "
                f"WHERE (lat,lon) IN ({marks_}) AND t BETWEEN ? AND ? "
                f"GROUP BY source", params).fetchall():
            counts[r["source"]] = r["n"]
        total = sum(counts.values()) or 1
        for src, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            flag = "  <-- INVENTED" if src in ("synthetic", "none") else ""
            print(f"  {str(src):12s} {n:6d} rows "
                  f"({100.0 * n / total:5.1f}%){flag}")
        bad = sum(n for s, n in counts.items() if s in ("synthetic", "none"))
        print(f"  track touches {len(cells)} cell(s) the boat actually sailed")
        print(f"  => {'CLEAN — every wind row it sailed was recorded' if not bad
                     else f'{bad} INVENTED wind row(s) in the sailed cells'}")
    print("\n(read-only: this script changed nothing)")


if __name__ == "__main__":
    main()
