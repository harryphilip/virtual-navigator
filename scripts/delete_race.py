"""Delete a race and everything attached to it, from the server console.

    .venv/bin/python scripts/delete_race.py <race_id>

On Fly:  fly ssh console -C "python /app/scripts/delete_race.py 1"

Removes the race, its marks, virtual boats (routes, logs, tracks), real
boats and their tracks, documents, and forecast snapshots.  Prints what it
deleted; irreversible, so it shows the race name first.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print(__doc__)
        sys.exit(1)
    race_id = int(sys.argv[1])
    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not r:
        print(f"no race with id {race_id}")
        sys.exit(1)
    boats = [b["id"] for b in db.execute(
        "SELECT id FROM boats WHERE race_id=?", (race_id,))]
    rbs = [b["id"] for b in db.execute(
        "SELECT id FROM real_boats WHERE race_id=?", (race_id,))]
    for bid in boats:
        for t in ("route_wps", "route_log", "track"):
            db.execute(f"DELETE FROM {t} WHERE boat_id=?", (bid,))
    for rb in rbs:
        db.execute("DELETE FROM real_track WHERE rb_id=?", (rb,))
    counts = {}
    for t, k in (("boats", "race_id"), ("real_boats", "race_id"),
                 ("marks", "race_id"), ("race_docs", "race_id"),
                 ("forecast_snapshots", "race_id"), ("races", "id")):
        counts[t] = db.execute(f"DELETE FROM {t} WHERE {k}=?", (race_id,)).rowcount
    db.commit()
    print(f"deleted race {race_id}: {r['name']}")
    print(f"  boats={counts['boats']} real_boats={counts['real_boats']} "
          f"marks={counts['marks']} docs={counts['race_docs']} "
          f"forecasts={counts['forecast_snapshots']}")


if __name__ == "__main__":
    main()
