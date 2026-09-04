"""Replace the polar a race is sailed on, from the server console.

    .venv/bin/python scripts/set_polar.py <race_id> <polar file> [polar name]

On Fly:  fly ssh console -C "python /app/scripts/set_polar.py 3 \
             /app/data/polar_imoca60.pol 'IMOCA Open 60 (non-foiling)'"

Use it when the numbers a race is sailing are simply wrong for the class —
a polar far off the boats it claims to represent makes the leaderboard and
the real-vs-virtual split meaningless.

The polar is parsed and sanity-checked before anything is written; a file
that will not parse, or whose peak speed is implausible, is refused.

Changing the polar mid-race does NOT rewrite what has already been sailed:
time never rewinds, so boats keep the track they made under the old numbers
and sail the new ones from here.  To re-sail a boat from the gun under the
new polar, follow this with restart_boat.py, which replays it through the
same cached weather.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db
from vn.polar import Polar

# a polar whose best speed in a working breeze falls outside this range is
# almost certainly the wrong file (or the wrong units) — refuse it rather
# than quietly hand a fleet numbers nobody can sail
MIN_PEAK_20KN = 6.0
MAX_PEAK_20KN = 40.0


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    race_id, path = int(sys.argv[1]), sys.argv[2]
    name = sys.argv[3] if len(sys.argv) == 4 else os.path.basename(path)

    try:
        text = open(path).read()
    except OSError as e:
        print(f"cannot read {path}: {e}")
        sys.exit(1)
    try:
        polar = Polar.parse(text, name)
    except ValueError as e:
        print(f"refusing: {path} does not parse as a polar — {e}")
        sys.exit(1)

    peak20 = max(polar.speed(a, 20) for a in range(0, 181, 5))
    if not MIN_PEAK_20KN <= peak20 <= MAX_PEAK_20KN:
        print(f"refusing: peak speed in 20 kn TWS is {peak20:.1f} kn, outside "
              f"the plausible {MIN_PEAK_20KN:.0f}–{MAX_PEAK_20KN:.0f} kn — "
              "wrong file, or speeds in the wrong units?")
        sys.exit(1)

    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not r:
        print(f"no race with id {race_id}")
        sys.exit(1)

    old = Polar.parse(r["polar_text"], r["polar_name"])
    old20 = max(old.speed(a, 20) for a in range(0, 181, 5))
    sailing = db.execute(
        "SELECT COUNT(*) c FROM boats WHERE race_id=? AND sim_time>?",
        (race_id, r["start_time"])).fetchone()["c"]

    db.execute("UPDATE races SET polar_text=?, polar_name=? WHERE id=?",
               (text, name, race_id))
    add_race_log(db, race_id,
                 f"Race polar replaced: {r['polar_name']} → {name} "
                 f"(peak in 20 kn TWS {old20:.1f} → {peak20:.1f} kn). "
                 "The old numbers were not this class's; boats sail the new "
                 "ones from here."
                 + (f" {sailing} boat{'s' if sailing != 1 else ''} already sailing keep the track they "
                    "made under the old polar unless restarted."
                    if sailing else ""))
    db.commit()
    print(f"{r['name']}: polar {r['polar_name']!r} -> {name!r}")
    print(f"  peak speed in 20 kn TWS: {old20:.1f} kn -> {peak20:.1f} kn")
    if sailing:
        print(f"  {sailing} boat(s) already sailing — run restart_boat.py on "
              "each to re-sail from the gun under the new polar")


if __name__ == "__main__":
    main()
