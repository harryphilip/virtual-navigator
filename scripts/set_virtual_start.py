"""Set a race's virtual start by hand — the moment the real fleet actually
started, when the tracker could not show it.

    .venv/bin/python scripts/set_virtual_start.py <race_id> <ISO8601 UTC time|auto>

On Fly:  fly ssh console -C "python /app/scripts/set_virtual_start.py 2 2026-09-04T16:20:00Z"

The fleet gate (vn/fleetgate.py) decides the virtual start from the first
fixes after the gun.  When the feed was down over the start, that decision
is the moment the feed came back, hours late; the committee sets the real
one here from the scratch sheet.  'auto' clears the decision so the gate
decides again from the tracks on file.  Boats already sailing are not
moved — restart_boat.py replays one from the new virtual start; boats
waiting on the line go off from it on the next tick.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db
from vn.fleetgate import fleet_gate, stamp


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        sys.exit(1)
    race_id, arg = int(argv[0]), argv[1].strip()
    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not race:
        print(f"no race with id {race_id}")
        sys.exit(1)
    if arg.lower() == "auto":
        db.execute("UPDATE races SET virtual_start=NULL WHERE id=?", (race_id,))
        race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
        gate = fleet_gate(db, race)
        msg = ("Virtual start left to the fleet gate again"
               + (f": {gate['started']} of {gate['fleet']} real boats seen after the gun, "
                  f"{gate['needed']} needed" if gate else " (no real fleet: the gun)") + ".")
    else:
        when = dt.datetime.fromisoformat(arg.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        t = int(when.timestamp())
        if t < race["start_time"]:
            print(f"refusing: {stamp(t)} is before the gun {stamp(race['start_time'])}")
            sys.exit(1)
        db.execute("UPDATE races SET virtual_start=? WHERE id=?", (t, race_id))
        msg = (f"Virtual start set by the committee to {stamp(t)}: virtual boats waiting "
               "on the line go off from then; boats already sailing are not moved.")
    add_race_log(db, race_id, msg)
    db.commit()
    print(f"{race['name']}: {msg}")


if __name__ == "__main__":
    main()
