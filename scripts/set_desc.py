"""Rewrite part of a race description from the server console.

    .venv/bin/python scripts/set_desc.py <race_id> <old substring> <new substring>

On Fly:  fly ssh console -C "python /app/scripts/set_desc.py 3 'old text' 'new text'"

Substring replace, so course facts can be corrected mid-race without
retyping the whole blurb. Prints the updated description.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    race_id, old, new = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not r:
        print(f"no race with id {race_id}")
        sys.exit(1)
    if old not in (r["description"] or ""):
        print("old substring not found in the description — nothing changed")
        sys.exit(1)
    db.execute("UPDATE races SET description=replace(description,?,?) WHERE id=?",
               (old, new, race_id))
    add_race_log(db, race_id, "Race description updated.")
    db.commit()
    d = db.execute("SELECT description FROM races WHERE id=?",
                   (race_id,)).fetchone()["description"]
    print(d)


if __name__ == "__main__":
    main()
