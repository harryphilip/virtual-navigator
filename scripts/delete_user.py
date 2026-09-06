"""Delete an account from the server console (a deletion request by email,
or a navigator who cannot sign in to do it themselves).

    .venv/bin/python scripts/delete_user.py <username>

On Fly:  fly ssh console -C "python /app/scripts/delete_user.py somebody"

Removes the user row, sessions, reset tokens, and every boat the account
entered with its routes, tracks and submission log; race logs keep their
entries. Same code path as the "Delete my account" button (vn.db.delete_user).
The last admin cannot be deleted.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import delete_user, get_db


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    username = sys.argv[1].strip().lower()
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not u:
        print(f"no account called {username!r}")
        sys.exit(1)
    try:
        n = delete_user(db, u["id"])
    except ValueError as e:
        print(e)
        sys.exit(1)
    print(f"deleted {username}: {n} boat(s) withdrawn")


if __name__ == "__main__":
    main()
