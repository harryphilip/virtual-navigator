"""Append an entry to a race's public committee log.

    .venv/bin/python scripts/log_note.py <race_id> <message> [ISO8601 time]

On Fly:  fly ssh console -C "python /app/scripts/log_note.py 3 'Start postponed …'"

The committee log is shown on the race page — record the why, not just
the what. The optional timestamp backdates an entry (documenting an
action after the fact); default is now.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    race_id, message = int(sys.argv[1]), sys.argv[2].strip()
    at = None
    if len(sys.argv) == 4:
        when = dt.datetime.fromisoformat(sys.argv[3].replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        at = int(when.timestamp())
    if not message:
        print("empty message")
        sys.exit(1)
    db = get_db()
    if not db.execute("SELECT 1 FROM races WHERE id=?", (race_id,)).fetchone():
        print(f"no race with id {race_id}")
        sys.exit(1)
    add_race_log(db, race_id, message, at)
    db.commit()
    stamp = dt.datetime.fromtimestamp(
        at or dt.datetime.now(dt.timezone.utc).timestamp(), dt.timezone.utc)
    print(f"logged at {stamp:%Y-%m-%d %H:%M}Z: {message}")


if __name__ == "__main__":
    main()
