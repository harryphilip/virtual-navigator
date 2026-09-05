"""Set how much of the real fleet must be under way before virtual boats start.

    .venv/bin/python scripts/set_fleet_gate.py <race_id> <percent>

On Fly:  fly ssh console -C "python /app/scripts/set_fleet_gate.py 2 5"

Every race with a tracked real fleet holds its virtual boats on the line
until this share of the real boats has been seen under way after the gun
(vn/fleetgate.py; 5% unless changed here).  0 switches the gate off: boats
waiting on the line start at once, at the gun or now, whichever is later.
A virtual start already decided is kept; move the gun with set_start.py to
decide it again.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db
from vn.fleetgate import fleet_gate
from vn.sim import get_marks


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        sys.exit(1)
    race_id, pct = int(argv[0]), float(argv[1])
    if not 0 <= pct <= 100:
        print("percent must be 0-100")
        sys.exit(1)
    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not race:
        print(f"no race with id {race_id}")
        sys.exit(1)
    db.execute("UPDATE races SET fleet_start_pct=? WHERE id=?", (pct, race_id))
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if pct == 0:
        marks = get_marks(db, race_id)
        start_at = max(race["start_time"], int(time.time()))
        n = db.execute(
            "UPDATE boats SET sim_time=?, lat=?, lon=?, next_mark=1 WHERE race_id=? "
            "AND sim_time IS NULL AND id IN (SELECT boat_id FROM route_wps)",
            (start_at, marks[0]["lat"], marks[0]["lon"], race_id)).rowcount
        msg = (f"Virtual start no longer waits for the real fleet; {n} boat"
               f"{'s' if n != 1 else ''} waiting on the line sent off.")
    else:
        gate = fleet_gate(db, race)
        msg = (f"Virtual boats start once {pct:g}% of the real fleet is under way"
               + (f" ({gate['needed']} of {gate['fleet']} boats; {gate['started']} so far)."
                  if gate else " — no real fleet is entered yet."))
        if race["virtual_start"]:
            msg += " The virtual start already decided stands."
    add_race_log(db, race_id, msg)
    db.commit()
    print(f"{race['name']}: {msg}")


if __name__ == "__main__":
    main()
