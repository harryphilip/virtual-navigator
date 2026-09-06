"""The practice race: always something to enter.

Real races have a gun and close entries at it, so between guns a new
navigator has nothing to sail. The practice course is a rolling-start race
(races.rolling): entries stay open for ROLLING_ENTRY_DAYS after its gun and
each boat starts the moment its first route is submitted, so a boat is on
the water within minutes of signing up. Finishers rank by elapsed time.

The ticker calls ensure_practice() every minute: when no rolling race is
taking entries, a fresh edition is created from data/races/practice_*.json
with its gun at the top of the current hour. Nothing else about the race is
special; it sails the same engine and weather as the rest.
"""
import datetime as dt
import glob
import logging
import os

from .db import add_race_log
from .races import create_race, load_definition

log = logging.getLogger("vn.practice")

PRACTICE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "races")
ENTRY_DAYS = 28                       # keep in step with app.ROLLING_ENTRY_DAYS


def definition_path():
    """The practice definition, or None when the deployment ships none."""
    files = sorted(glob.glob(os.path.join(PRACTICE_DIR, "practice_*.json")))
    return files[0] if files else None


def open_practice(db, now):
    """The rolling race still taking entries, if any."""
    return db.execute(
        "SELECT * FROM races WHERE rolling=1 AND start_time + ? > ? "
        "ORDER BY start_time DESC LIMIT 1", (ENTRY_DAYS * 86400, now)).fetchone()


def edition_name(base, when):
    d = dt.datetime.fromtimestamp(when, dt.timezone.utc)
    return f"{base} · {d.strftime('%b %Y')}"


def ensure_practice(db, now, path=None):
    """Open a new practice edition when none is taking entries. Returns the
    new race id, or None when nothing needed doing."""
    path = path or definition_path()
    if not path or open_practice(db, now):
        return None
    d = load_definition(path)
    gun = now - now % 3600
    name = edition_name(d["name"], gun)
    if db.execute("SELECT 1 FROM races WHERE name=?", (name,)).fetchone():
        name = f"{name} ({dt.datetime.fromtimestamp(gun, dt.timezone.utc):%d %b})"
    d = dict(d, rolling=True)
    race_id = create_race(db, d, name=name, start_time=gun)
    add_race_log(db, race_id,
                 f"Practice edition opened. Entries stay open for {ENTRY_DAYS} days; "
                 "every boat starts when its navigator submits a route, and finishers "
                 "rank by elapsed time.")
    db.commit()
    log.info("practice race %s opened: %s", race_id, name)
    return race_id
