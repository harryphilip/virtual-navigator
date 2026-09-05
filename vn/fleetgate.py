"""Virtual boats start when the real fleet does.

A race with a tracked real fleet (YB or AIS) gates its virtual start on
that fleet: virtual boats wait on the line until at least
races.fleet_start_pct of the real boats have been seen under way after the
gun, and then start at the moment the last of those was first seen.  A
postponed or delayed start delays the virtual fleet with it, instead of
sending it off at the scheduled gun into a race the real boats have not
begun.  The decision is recorded once (races.virtual_start) so a later
track correction cannot move a start already sailed.  Races without a real
fleet, or with the gate set to 0, start at the gun as before.  Boats that
were already sailing when the gate opened are never moved; the committee
replays one from the fleet's start with scripts/restart_boat.py.
"""
import datetime as dt
import math

from .db import add_race_log
from .sim import get_marks

DEFAULT_PCT = 5.0


def stamp(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d %b %H:%M") + "Z"


def fleet_gate(db, race):
    """The gate for a race, or None when it does not apply (no real fleet,
    or fleet_start_pct is 0).

    {"pct", "fleet", "needed", "started", "open_at"} — started counts real
    boats with a fix after the gun; open_at is when the needed-th of them
    was first seen, None while the gate still holds."""
    pct = race["fleet_start_pct"]
    if pct is None:
        pct = DEFAULT_PCT
    if pct <= 0:
        return None
    fleet = db.execute("SELECT COUNT(*) c FROM real_boats WHERE race_id=?",
                       (race["id"],)).fetchone()["c"]
    if not fleet:
        return None
    needed = max(1, math.ceil(fleet * pct / 100.0))
    firsts = [r["t0"] for r in db.execute(
        "SELECT MIN(t) t0 FROM real_track WHERE t>=? AND rb_id IN "
        "(SELECT id FROM real_boats WHERE race_id=?) GROUP BY rb_id ORDER BY t0",
        (race["start_time"], race["id"]))]
    if race["virtual_start"]:
        open_at = race["virtual_start"]
    else:
        open_at = firsts[needed - 1] if len(firsts) >= needed else None
    return {"pct": pct, "fleet": fleet, "needed": needed,
            "started": len(firsts), "open_at": open_at}


def virtual_start(db, race):
    """When virtual boats start: the gun, or the fleet's start once the gate
    has opened; None while the gate holds them on the line."""
    if race["virtual_start"]:
        return race["virtual_start"]
    gate = fleet_gate(db, race)
    if gate is None:
        return race["start_time"]
    return gate["open_at"]


def open_gate(db, race, now=None):
    """Record the fleet's start once it is known and send the boats waiting
    on the line off from it.  Returns the virtual start if the gate opened
    on this call, else None."""
    if race["virtual_start"]:
        return None
    gate = fleet_gate(db, race)
    if gate is None or gate["open_at"] is None:
        return None
    t = gate["open_at"]
    marks = get_marks(db, race["id"])
    db.execute("UPDATE races SET virtual_start=? WHERE id=?", (t, race["id"]))
    waiting = db.execute(
        "UPDATE boats SET sim_time=?, lat=?, lon=?, next_mark=1 WHERE race_id=? "
        "AND sim_time IS NULL AND id IN (SELECT boat_id FROM route_wps)",
        (t, marks[0]["lat"], marks[0]["lon"], race["id"])).rowcount
    add_race_log(db, race["id"],
                 f"Fleet under way: {gate['started']} of {gate['fleet']} real boats seen "
                 f"after the gun ({gate['needed']} needed). Virtual boats start at "
                 f"{stamp(t)}; {waiting} waiting on the line sent off. Boats already "
                 "sailing are not moved.")
    db.commit()
    return t
