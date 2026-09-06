"""Create a race from a JSON definition, from the server console.

    .venv/bin/python scripts/create_race.py data/races/<race>.json

On Fly:  fly ssh console -C "python /app/scripts/create_race.py /app/data/races/<race>.json"

The JSON holds the race settings and marks; the polar comes from an inline
"polar_text" or a "polar_file" path relative to the JSON file.  A mark may
carry "side": "port" or "stbd" — the side boats must leave it on; routings
that pass it the wrong way are rebuilt into a rounding on submission.
"rolling": true makes a rolling-start race (enter any time, start when you
submit a route) — the practice race opens itself that way from the ticker,
so this script is for the real-race definitions.  Prints the race id.
Refuses to create a second race with the same name.  Race management goes
through admin accounts (make_admin.py).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db
from vn.races import create_race, load_definition


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    d = load_definition(sys.argv[1])
    try:
        race_id = create_race(get_db(), d)
    except ValueError as e:
        print(f"{e} — nothing done")
        sys.exit(1)
    print(f"race {race_id}: {d['name']}")
    print(f"start: {d['start_time']}  marks: {len(d['marks'])}"
          f"{'  rolling start' if d.get('rolling') else ''}")


if __name__ == "__main__":
    main()
