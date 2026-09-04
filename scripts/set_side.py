"""Set which side boats must leave a course mark on, from the server console.

    .venv/bin/python scripts/set_side.py <race_id> <seq> <port|stbd|none>

On Fly:  fly ssh console -C "python /app/scripts/set_side.py 2 1 stbd"

Only the mark row changes.  The side is enforced when a routing is next
reconciled — on submission or a restart_boat.py — where a pass on the
wrong side, a touch-and-go, or a leg straight over the mark is rebuilt as
a rounding on the correct side.  'none' clears it (either side allowed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db
from vn.sim import SIDE_WORD


def main():
    if len(sys.argv) != 4 or sys.argv[3] not in ("port", "stbd", "none"):
        print(__doc__)
        sys.exit(1)
    race_id, seq = int(sys.argv[1]), int(sys.argv[2])
    side = None if sys.argv[3] == "none" else sys.argv[3]
    db = get_db()
    m = db.execute("SELECT * FROM marks WHERE race_id=? AND seq=?",
                   (race_id, seq)).fetchone()
    if not m:
        print(f"race {race_id} has no mark seq {seq}")
        sys.exit(1)
    db.execute("UPDATE marks SET side=? WHERE race_id=? AND seq=?",
               (side, race_id, seq))
    what = (f"must be left to {SIDE_WORD[side]}" if side
            else "may be passed on either side")
    add_race_log(db, race_id, f"Course mark '{m['name']}' {what}. Routings are "
                 "checked for it when submitted or restarted.")
    db.commit()
    print(f"{m['name']}: {what}")


if __name__ == "__main__":
    main()
