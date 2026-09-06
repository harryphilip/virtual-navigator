"""Create a race from a JSON definition (the files in data/races/).

Shared by scripts/create_race.py (the console) and vn/practice.py (the
ticker, which opens a fresh practice edition when the last one's entries
close). The definition holds the settings and marks; the polar comes from
an inline "polar_text" or a "polar_file" path relative to the file.
"""
import datetime as dt
import json
import os
import time

from .polar import Polar
from .sim import race_settings


def load_definition(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if "polar_text" not in d:
        with open(os.path.join(os.path.dirname(os.path.abspath(path)),
                               d["polar_file"]), encoding="utf-8") as f:
            d["polar_text"] = f.read()
    return d


def create_race(db, d, *, name=None, start_time=None):
    """Insert the race and its marks; commits; returns the race id. `name`
    and `start_time` (unix seconds) override the definition. Raises
    ValueError for a bad definition or a name already in use."""
    name = (name or d["name"]).strip()
    polar_text = d["polar_text"]
    Polar.parse(polar_text)                      # validate before touching the DB
    if start_time is None:
        start_time = int(dt.datetime.fromisoformat(
            d["start_time"].replace("Z", "+00:00")).timestamp())
    marks = d["marks"]
    if len(marks) < 2:
        raise ValueError("need at least start and finish marks")
    for m in marks:
        if (m.get("side") or None) not in (None, "port", "stbd"):
            raise ValueError(f"mark {m['name']!r}: side must be port/stbd")
    s = race_settings(d)                         # raises on an out-of-range value
    if db.execute("SELECT 1 FROM races WHERE name=?", (name,)).fetchone():
        raise ValueError(f"a race named {name!r} already exists")
    cur = db.execute(
        "INSERT INTO races(name,description,start_time,perf_factor,step_minutes,"
        "mark_radius_nm,polar_name,polar_text,admin_key,created_at,"
        "maneuver_penalty_s,currents_enabled,grounding_depth_ft,docs_url,rolling) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, d.get("description", ""), int(start_time),
         s["perf_factor"], s["step_minutes"], s["mark_radius_nm"],
         d.get("polar_name", "race polar"), polar_text, "", int(time.time()),
         s["maneuver_penalty_s"], s["currents_enabled"], s["grounding_depth_ft"],
         d.get("docs_url", ""), 1 if d.get("rolling") else 0))
    race_id = cur.lastrowid
    for i, m in enumerate(marks):
        db.execute("INSERT INTO marks(race_id,seq,name,lat,lon,side) VALUES (?,?,?,?,?,?)",
                   (race_id, i, m["name"], float(m["lat"]), float(m["lon"]),
                    m.get("side") or None))
    db.commit()
    return race_id
