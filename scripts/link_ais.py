"""Put a race's real fleet on the leaderboard from a roster, tracked over AIS.

    .venv/bin/python scripts/link_ais.py <race_id> <roster.csv> [racing_area]

On Fly:  fly ssh console -C "python /app/scripts/link_ais.py 2 /app/data/races/vineyard_2026_roster.csv Vineyard"

The roster CSV (columns: area,class,sail,name,type) is the event's
scratch sheet — see data/races/*_roster.csv.  With a racing_area
only that course's boats are entered.  Each boat becomes a real_boats row;
the race is flagged `ais`, and the server's AIS feed (AISSTREAM_KEY,
vn/ais.py) binds each row to a transponder as soon as a vessel of that
name broadcasts inside the course box (sponsor suffixes and 'YCC' tags
are ignored); scripts/set_mmsi.py binds a boat by hand.  Re-running
refreshes the roster; boats already linked keep their MMSI and track.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    race_id, path = int(sys.argv[1]), sys.argv[2]
    area = sys.argv[3].strip().lower() if len(sys.argv) == 4 else ""
    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not race:
        print(f"no race with id {race_id}")
        sys.exit(1)
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if not area or (r.get("area") or "").strip().lower() == area]
    if not rows:
        print("no roster rows matched")
        sys.exit(1)

    added = kept = 0
    for r in rows:
        name = r["name"].strip()[:60]
        klass = (r.get("type") or "").strip()[:60]
        sail = (r.get("sail") or "").strip()[:20]
        row = db.execute("SELECT id FROM real_boats WHERE race_id=? AND name=?",
                         (race_id, name)).fetchone()
        if row:
            db.execute("UPDATE real_boats SET klass=?, sail_no=? WHERE id=?",
                       (klass, sail, row["id"]))
            kept += 1
        else:
            db.execute("INSERT INTO real_boats(race_id,name,klass,sail_no) "
                       "VALUES (?,?,?,?)", (race_id, name, klass, sail))
            added += 1
    db.execute("UPDATE races SET ais=1 WHERE id=?", (race_id,))
    add_race_log(db, race_id,
                 f"Real fleet entered from the scratch sheet: {len(rows)} boat{'s' if len(rows) != 1 else ''}"
                 f"{' on the ' + sys.argv[3].strip() + ' course' if area else ''}, "
                 "followed over AIS. Each shows as not started until its transponder "
                 "is heard on the course.")
    db.commit()
    print(f"{race['name']}: {added} boat(s) added, {kept} refreshed, AIS on")
    if not os.environ.get("AISSTREAM_KEY"):
        print("note: AISSTREAM_KEY is not set in this environment — the server "
              "needs it (fly secrets set AISSTREAM_KEY=…) to hear anything")


if __name__ == "__main__":
    main()
