"""Link a YB Tracking race to a race from the server console.

    .venv/bin/python scripts/link_yb.py <race_id> <yb_slug> [exclude,substrings]

On Fly:  fly ssh console -C "python /app/scripts/link_yb.py 3 tora2026 secondary,zz_"

Imports the fleet roster (skipping boats whose name contains any of the
comma-separated exclude substrings, case-insensitive — backup trackers,
spares), stores the slug so the background poller keeps positions fresh,
and ingests the full track history.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn import yb
from vn.db import add_race_log, get_db
from vn.realfleet import ingest_points
from vn.sim import get_marks


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    race_id, slug = int(sys.argv[1]), sys.argv[2].strip().strip("/")
    excl = [s.strip().lower() for s in
            (sys.argv[3] if len(sys.argv) == 4 else "").split(",") if s.strip()]
    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not race:
        print(f"no race with id {race_id}")
        sys.exit(1)

    setup = yb.race_setup(slug)
    teams = [t for t in setup["teams"]
             if not any(x in t["name"].lower() for x in excl)]
    if not teams:
        print("no teams matched")
        sys.exit(1)
    print(f"{setup['title']}: {len(teams)} teams "
          f"({len(setup['teams']) - len(teams)} excluded)")

    marks = get_marks(db, race_id)
    by_yb = {}
    for t in teams:
        name = t["name"][:60] or f"YB {t['yb_id']}"
        row = db.execute("SELECT id FROM real_boats WHERE race_id=? AND name=?",
                         (race_id, name)).fetchone()
        if row:
            db.execute("UPDATE real_boats SET yb_id=?, klass=? WHERE id=?",
                       (t["yb_id"], t["model"][:60], row["id"]))
            by_yb[t["yb_id"]] = row["id"]
        else:
            cur = db.execute(
                "INSERT INTO real_boats(race_id,name,klass,yb_id) VALUES (?,?,?,?)",
                (race_id, name, t["model"][:60], t["yb_id"]))
            by_yb[t["yb_id"]] = cur.lastrowid
    db.execute("UPDATE races SET yb_slug=? WHERE id=?", (slug, race_id))
    add_race_log(db, race_id,
                 f"Live tracker linked: yb.tl/{slug}, {len(teams)} real "
                 "boat(s) on the leaderboard, full track history imported.")
    db.commit()

    imported = 0
    for yb_id, pts in yb.positions(slug).items():
        rb_id = by_yb.get(yb_id)
        if rb_id and pts:
            imported += ingest_points(db, race, marks, rb_id, pts)
    print(f"linked yb.tl/{slug}: {imported} positions imported")


if __name__ == "__main__":
    main()
