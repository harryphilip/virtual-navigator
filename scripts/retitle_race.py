"""Retitle a live race: set its name and description from the server console.

    .venv/bin/python scripts/retitle_race.py <race_id> data/races/<race>.json
    .venv/bin/python scripts/retitle_race.py <race_id> --name "New York to Lorient" \
        [--desc "Sailed alongside the IMOCA fleet of The Ocean Race Atlantic ..."]

On Fly:  fly ssh console -C "python /app/scripts/retitle_race.py 3 /app/data/races/ocean_race_atlantic_2026.json"

With a race JSON the name and description are copied from the file, so the
committed definition stays the single source of truth; --name/--desc set
them directly for a race that has no JSON.  Races are titled by their
course ("Malta round Sicily 606"), never by a sponsor's mark; the official
race is named once, factually, in the first line of the description
("Sailed alongside the fleet of the ...").  The change is written to the
public race log with the old title, and the state cache is left to expire
on the next tick.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("race_id", type=int)
    ap.add_argument("json_file", nargs="?", help="race definition to copy name/description from")
    ap.add_argument("--name")
    ap.add_argument("--desc")
    a = ap.parse_args()

    if a.json_file:
        with open(a.json_file, encoding="utf-8") as f:
            d = json.load(f)
        name, desc = d["name"], d.get("description")
    else:
        name, desc = a.name, a.desc
    if not name:
        ap.error("give a race JSON or --name")

    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (a.race_id,)).fetchone()
    if not r:
        print(f"no race with id {a.race_id}")
        sys.exit(1)
    clash = db.execute("SELECT id FROM races WHERE name=? AND id<>?",
                       (name, a.race_id)).fetchone()
    if clash:
        print(f"race {clash['id']} is already named {name!r} — nothing done")
        sys.exit(1)

    old = r["name"]
    if desc is None:
        db.execute("UPDATE races SET name=? WHERE id=?", (name, a.race_id))
    else:
        db.execute("UPDATE races SET name=?, description=? WHERE id=?",
                   (name, desc, a.race_id))
    if old != name:
        add_race_log(db, a.race_id, f"Race retitled: “{old}” is now “{name}”.")
    elif desc is not None:
        add_race_log(db, a.race_id, "Race description updated.")
    db.commit()
    r = db.execute("SELECT name, description FROM races WHERE id=?",
                   (a.race_id,)).fetchone()
    print(f"race {a.race_id}: {r['name']}")
    print(r["description"] or "")


if __name__ == "__main__":
    main()
