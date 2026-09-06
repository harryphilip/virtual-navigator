"""Remove a virtual boat from a race, from the server console.

    .venv/bin/python scripts/remove_boat.py <race_id> <boat name> [reason]

On Fly:  fly ssh console -C "python /app/scripts/remove_boat.py 9 'Some Name' 'offensive name'"

For a boat name that impersonates a real yacht or a person, or is
offensive (terms: Fair sailing). Deletes the boat with its routes, track
and submission log and writes "<name> removed by the admin (<reason>)." to
the public race log. The navigator keeps their account and may enter
again under another name while entries are open.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import delete_boat, get_db


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    race_id, name = int(sys.argv[1]), sys.argv[2]
    reason = sys.argv[3] if len(sys.argv) == 4 else "name not allowed"
    db = get_db()
    b = db.execute("SELECT id, name FROM boats WHERE race_id=? AND name=?",
                   (race_id, name)).fetchone()
    if not b:
        names = [r["name"] for r in db.execute(
            "SELECT name FROM boats WHERE race_id=? ORDER BY name", (race_id,))]
        print(f"no virtual boat {name!r} in race {race_id}; boats: {', '.join(names) or '(none)'}")
        sys.exit(1)
    delete_boat(db, b["id"], f"removed by the admin ({reason})")
    db.commit()
    print(f"race {race_id}: {b['name']} removed ({reason})")


if __name__ == "__main__":
    main()
