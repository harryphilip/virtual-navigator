"""Import exclusion zones for a race from its YB tracker's course drawing.

    .venv/bin/python scripts/set_zones.py <race_id> [yb_slug]

On Fly:  fly ssh console -C "python /app/scripts/set_zones.py 3"

Reads every polygon drawn on the YB race (exclusion zones, TSS boxes, ice
limits — start/finish lines are skipped) and stores them on the race.
Virtual boats caught inside sail at half speed, mirroring the grounding
rule. With no slug argument the race's linked yb_slug is used. Re-running
replaces the stored zones.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn import yb
from vn.db import add_race_log, get_db


def main():
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(1)
    race_id = int(sys.argv[1])
    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not r:
        print(f"no race with id {race_id}")
        sys.exit(1)
    slug = sys.argv[2] if len(sys.argv) == 3 else (r["yb_slug"] or "")
    if not slug:
        print("race has no linked yb_slug — pass one explicitly")
        sys.exit(1)

    zones = yb.zones(slug)
    if not zones:
        print(f"yb.tl/{slug} draws no polygons")
        sys.exit(1)
    db.execute("UPDATE races SET zones_json=? WHERE id=?",
               (json.dumps(zones), race_id))
    add_race_log(db, race_id,
                 f"{len(zones)} exclusion zone(s) imported from "
                 f"yb.tl/{slug}: {', '.join(z['name'] for z in zones)}. "
                 "Virtual boats inside sail at half speed.")
    db.commit()
    print(f"{r['name']}: {len(zones)} zone(s) stored")
    for z in zones:
        print(f"  ⛔ {z['name']} ({len(z['pts'])} vertices)")


if __name__ == "__main__":
    main()
