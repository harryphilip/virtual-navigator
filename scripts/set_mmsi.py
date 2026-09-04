"""Bind (or unbind) a real boat's AIS transponder by hand.

    .venv/bin/python scripts/set_mmsi.py <race_id> <boat name> <mmsi|none> [--wipe]

On Fly:  fly ssh console -C "python /app/scripts/set_mmsi.py 2 Moneyball 367123456"
         fly ssh console -C "python /app/scripts/set_mmsi.py 2 Liberty none --wipe"

Use it when the name match picked the wrong vessel, or a boat broadcasts
under a name the roster doesn't carry.  'none' clears the binding so the
feed matches by name again.  The boat's stored track is kept unless
--wipe is given, which drops every stored point and puts the boat back to
not started — for when a wrong vessel's points crept in (they show as an
obviously wrong track on the map).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    wipe = "--wipe" in argv
    argv = [a for a in argv if a != "--wipe"]
    if len(argv) != 3:
        print(__doc__)
        sys.exit(1)
    race_id, name, arg = int(argv[0]), argv[1], argv[2].strip().lower()
    mmsi = None if arg == "none" else int(arg)
    db = get_db()
    rb = db.execute("SELECT * FROM real_boats WHERE race_id=? AND name=?",
                    (race_id, name)).fetchone()
    if not rb:
        print(f"race {race_id} has no real boat named {name!r}")
        sys.exit(1)
    if mmsi is not None:
        other = db.execute("SELECT name FROM real_boats WHERE race_id=? AND mmsi=? AND id<>?",
                           (race_id, mmsi, rb["id"])).fetchone()
        if other:
            db.execute("UPDATE real_boats SET mmsi=NULL WHERE race_id=? AND mmsi=?",
                       (race_id, mmsi))
            print(f"MMSI {mmsi} taken off {other['name']}")
    db.execute("UPDATE real_boats SET mmsi=? WHERE id=?", (mmsi, rb["id"]))
    what = f"bound to MMSI {mmsi}" if mmsi else "AIS binding cleared"
    if wipe:
        n = db.execute("DELETE FROM real_track WHERE rb_id=?", (rb["id"],)).rowcount
        db.execute("UPDATE real_boats SET last_t=NULL, last_lat=NULL, last_lon=NULL,"
                   " sog=NULL, next_mark=1, finished_at=NULL WHERE id=?", (rb["id"],))
        what += f", {n} stored point{'s' if n != 1 else ''} discarded"
    add_race_log(db, race_id, f"AIS: '{name}' {what} by the committee.")
    db.commit()
    print(f"{name}: {what}")


if __name__ == "__main__":
    main()
