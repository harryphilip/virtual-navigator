"""Create a race from a JSON definition, from the server console.

    .venv/bin/python scripts/create_race.py data/races/<race>.json

On Fly:  fly ssh console -C "python /app/scripts/create_race.py /app/data/races/<race>.json"

The JSON holds the race settings and marks; the polar comes from an inline
"polar_text" or a "polar_file" path relative to the JSON file.  A mark may
carry "side": "port" or "stbd" — the side boats must leave it on; routings
that pass it the wrong way are rebuilt into a rounding on submission.  Prints the
race id.  Refuses to create a second race with the same name.  Race
management goes through admin accounts (make_admin.py).
"""
import datetime as dt
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db
from vn.polar import Polar


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    d = json.load(open(path))
    if "polar_text" in d:
        polar_text = d["polar_text"]
    else:
        polar_text = open(os.path.join(os.path.dirname(os.path.abspath(path)),
                                       d["polar_file"])).read()
    Polar.parse(polar_text)                      # validate before touching the DB
    start = int(dt.datetime.fromisoformat(
        d["start_time"].replace("Z", "+00:00")).timestamp())
    marks = d["marks"]
    assert len(marks) >= 2, "need at least start and finish marks"

    db = get_db()
    if db.execute("SELECT 1 FROM races WHERE name=?", (d["name"],)).fetchone():
        print(f"a race named {d['name']!r} already exists — nothing done")
        sys.exit(1)
    admin_key = secrets.token_hex(12)
    cur = db.execute(
        "INSERT INTO races(name,description,start_time,perf_factor,step_minutes,"
        "mark_radius_nm,polar_name,polar_text,admin_key,created_at,"
        "maneuver_penalty_s,currents_enabled,grounding_depth_ft) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d["name"], d.get("description", ""), start,
         float(d.get("perf_factor", 0.9)), int(d.get("step_minutes", 10)),
         float(d.get("mark_radius_nm", 2.0)), d.get("polar_name", "race polar"),
         polar_text, admin_key, int(time.time()),
         float(d.get("maneuver_penalty_s", 120)),
         1 if d.get("currents_enabled", True) else 0,
         float(d.get("grounding_depth_ft", 15))))
    race_id = cur.lastrowid
    for i, m in enumerate(marks):
        side = m.get("side") or None
        assert side in (None, "port", "stbd"), f"mark {m['name']!r}: side must be port/stbd"
        db.execute("INSERT INTO marks(race_id,seq,name,lat,lon,side) VALUES (?,?,?,?,?,?)",
                   (race_id, i, m["name"], float(m["lat"]), float(m["lon"]), side))
    db.commit()
    print(f"race {race_id}: {d['name']}")
    print(f"start: {d['start_time']}  marks: {len(marks)}")


if __name__ == "__main__":
    main()
