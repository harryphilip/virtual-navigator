"""Assign an unowned (pre-account) boat to a navigator from the server console.

    .venv/bin/python scripts/assign_boat.py <boat name> <username>

On Fly:  fly ssh console -C "python /app/scripts/assign_boat.py Magpie somebody"

Only boats with no owner can be assigned — taking a boat away from an
account holder is deliberately not supported here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    boat_name, username = sys.argv[1], sys.argv[2].strip().lower()
    db = get_db()
    u = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not u:
        print(f"no user named {username!r}")
        sys.exit(1)
    cur = db.execute(
        "UPDATE boats SET owner_id=?, pin_hash='' WHERE name=? AND owner_id IS NULL",
        (u["id"], boat_name))
    db.commit()
    if cur.rowcount:
        print(f"{boat_name}: now owned by {username} ({cur.rowcount} boat(s))")
    else:
        print(f"no unowned boat named {boat_name!r} — already claimed, or check the name")
        sys.exit(1)


if __name__ == "__main__":
    main()
