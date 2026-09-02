"""Reschedule a race start from the server console (postponement, or fixing
a start entered against the wrong day — race committees do it too).

    .venv/bin/python scripts/set_start.py <race_id> <ISO8601 UTC time> \
        [--desc-replace OLD NEW]

On Fly:  fly ssh console -C "python /app/scripts/set_start.py 3 2026-09-02T20:30:00Z"

Only allowed before any virtual boat has started sailing — moving the gun
under a fleet already on the course would corrupt sim state.  An optional
--desc-replace OLD NEW rewrites the matching substring of the race
description so the blurb agrees with the new gun.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db


def main():
    args = sys.argv[1:]
    repl = None
    if "--desc-replace" in args:
        i = args.index("--desc-replace")
        repl = args[i + 1:i + 3]
        args = args[:i] + args[i + 3:]
        if len(repl) != 2:
            print(__doc__)
            sys.exit(1)
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    race_id = int(args[0])
    when = dt.datetime.fromisoformat(args[1].replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    start = int(when.timestamp())

    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not r:
        print(f"no race with id {race_id}")
        sys.exit(1)
    # boats waiting on the line have sim_time == start_time (set at first
    # routing, never advanced before the gun); anything beyond that has
    # actually sailed and the gun can no longer move
    sailing = db.execute(
        "SELECT COUNT(*) c FROM boats WHERE race_id=? AND sim_time>?",
        (race_id, r["start_time"])).fetchone()["c"]
    if sailing:
        print(f"refusing: {sailing} virtual boat(s) already sailing this race")
        sys.exit(1)

    old = dt.datetime.fromtimestamp(r["start_time"], dt.timezone.utc)
    # a gun in the past is fine: the engine replays the missed hours through
    # real (cached) historical weather on the next tick, exactly like the
    # demo race — routings on file predate the gun either way
    db.execute("UPDATE races SET start_time=? WHERE id=?", (start, race_id))
    moored = db.execute(
        "UPDATE boats SET sim_time=? WHERE race_id=? AND sim_time IS NOT NULL",
        (start, race_id)).rowcount
    if repl:
        db.execute("UPDATE races SET description=replace(description,?,?) "
                   "WHERE id=?", (repl[0], repl[1], race_id))
    db.commit()
    print(f"{r['name']}: start {old:%Y-%m-%d %H:%M}Z -> {when:%Y-%m-%d %H:%M}Z"
          + f", {moored} boat(s) re-anchored to the new gun"
          + (" (description updated)" if repl else ""))


if __name__ == "__main__":
    main()
