"""Move a course mark from the server console (e.g. to the real start line
published on the tracker once it's known).

    .venv/bin/python scripts/set_mark.py <race_id> <seq> <lat> <lon> [name]

On Fly:  fly ssh console -C "python /app/scripts/set_mark.py 3 0 39.6412 -71.25"

Only the mark row moves: boats keep their state and routings. A boat that
should restart from a moved start line gets scripts/restart_boat.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db


def main():
    if len(sys.argv) not in (5, 6):
        print(__doc__)
        sys.exit(1)
    race_id, seq = int(sys.argv[1]), int(sys.argv[2])
    lat, lon = float(sys.argv[3]), float(sys.argv[4])
    db = get_db()
    m = db.execute("SELECT * FROM marks WHERE race_id=? AND seq=?",
                   (race_id, seq)).fetchone()
    if not m:
        print(f"race {race_id} has no mark seq {seq}")
        sys.exit(1)
    name = sys.argv[5] if len(sys.argv) == 6 else m["name"]
    db.execute("UPDATE marks SET lat=?, lon=?, name=? WHERE race_id=? AND seq=?",
               (lat, lon, name, race_id, seq))
    db.commit()
    print(f"{name}: {m['lat']:.4f},{m['lon']:.4f} -> {lat:.4f},{lon:.4f}")


if __name__ == "__main__":
    main()
