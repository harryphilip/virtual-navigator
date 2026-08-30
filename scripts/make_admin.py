"""Grant (or revoke) the admin role from the server console.

    .venv/bin/python scripts/make_admin.py <username> [--revoke]

On Fly:  fly ssh console -C "python /app/scripts/make_admin.py <username>"
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    username = sys.argv[1].strip().lower()
    grant = 0 if "--revoke" in sys.argv else 1
    db = get_db()
    cur = db.execute("UPDATE users SET is_admin=? WHERE username=?", (grant, username))
    db.commit()
    if cur.rowcount:
        print(f"{username}: admin={'yes' if grant else 'no'}")
    else:
        print(f"no user named {username!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
