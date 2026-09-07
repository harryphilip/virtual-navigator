"""Insert a course mark into a live race, from the server console.

    .venv/bin/python scripts/add_mark.py <race_id> <seq> <lat> <lon> <name> [port|stbd]

On Fly:  fly ssh console -C "python /app/scripts/add_mark.py 5 2 48.95 -3.55 'Sept-Îles clearance'"

The new mark takes position <seq> (0 = before the start) and the marks
from there on move up one. Meant for a clearance mark that keeps the
straight-line default course at sea, or a rounding the sailing
instructions turn out to require. Refused once any boat has started: a
started boat's next-mark index would point at the wrong mark (move marks
with set_mark.py instead, or restart boats after). Logged to the race log.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db


def main():
    if len(sys.argv) not in (6, 7):
        print(__doc__)
        sys.exit(1)
    race_id, seq = int(sys.argv[1]), int(sys.argv[2])
    lat, lon, name = float(sys.argv[3]), float(sys.argv[4]), sys.argv[5].strip()
    side = sys.argv[6] if len(sys.argv) == 7 else None
    if side not in (None, "port", "stbd"):
        print("side must be port or stbd")
        sys.exit(1)
    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not r:
        print(f"no race with id {race_id}")
        sys.exit(1)
    started = db.execute("SELECT COUNT(*) c FROM boats WHERE race_id=? AND sim_time IS NOT NULL",
                         (race_id,)).fetchone()["c"]
    if started:
        print(f"{started} boat(s) have started in this race; inserting a mark would shift their "
              "next-mark index. Move marks with set_mark.py, or restart the boats afterwards.")
        sys.exit(1)
    n = db.execute("SELECT COUNT(*) c FROM marks WHERE race_id=?", (race_id,)).fetchone()["c"]
    if not 0 <= seq <= n:
        print(f"seq must be between 0 and {n}")
        sys.exit(1)
    # shift down from the end so the (race_id, seq) pairs never collide
    for s in range(n - 1, seq - 1, -1):
        db.execute("UPDATE marks SET seq=? WHERE race_id=? AND seq=?", (s + 1, race_id, s))
    db.execute("INSERT INTO marks(race_id,seq,name,lat,lon,side) VALUES (?,?,?,?,?,?)",
               (race_id, seq, name, lat, lon, side))
    db.execute("UPDATE boats SET next_mark=next_mark+1 WHERE race_id=? AND next_mark>?",
               (race_id, seq))
    add_race_log(db, race_id, f"Mark added at position {seq}: {name} ({lat:.4f}, {lon:.4f})"
                              f"{' to ' + side if side else ''}.")
    db.commit()
    print(f"race {race_id}: mark {seq} = {name} ({lat}, {lon}); {n + 1} marks now")


if __name__ == "__main__":
    main()
