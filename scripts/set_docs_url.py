"""Point a live race at the organiser's own documents page.

    .venv/bin/python scripts/set_docs_url.py <race_id> <url|none>

On Fly:  fly ssh console -C "python /app/scripts/set_docs_url.py 2 https://www.stormtrysail.org/"

The race card links it as "Official race documents". Notices of race and
sailing instructions are the organiser's to publish and are amended, so
the site links to them rather than serving copies (uploaded copies stay
visible to admins only). 'none' removes the link. Logged to the race log.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import add_race_log, get_db


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    race_id, url = int(sys.argv[1]), sys.argv[2].strip()
    if url.lower() == "none":
        url = ""
    elif not url.startswith(("http://", "https://")):
        print("give a full http(s) URL, or 'none'")
        sys.exit(1)
    db = get_db()
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not r:
        print(f"no race with id {race_id}")
        sys.exit(1)
    db.execute("UPDATE races SET docs_url=? WHERE id=?", (url, race_id))
    add_race_log(db, race_id, f"Official documents link set to {url}." if url
                 else "Official documents link removed.")
    db.commit()
    print(f"race {race_id}: docs_url = {url or '(none)'}")


if __name__ == "__main__":
    main()
