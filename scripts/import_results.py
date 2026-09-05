"""Import the committee's official results for a race's real fleet.

    .venv/bin/python scripts/import_results.py <race_id> yachtscoring:<eventId>[#race] [--apply]
    .venv/bin/python scripts/import_results.py <race_id> results.csv [--apply]

On Fly:  fly ssh console -C "python /app/scripts/import_results.py 2 yachtscoring:50775 --apply"

Without --apply it only prints the preview: which roster boats matched which
result rows, and what did not match on either side. With --apply the
matched boats get the committee's finish time, elapsed and corrected time,
status and places, and the committee log records the import.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn import results
from vn.db import get_db


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    argv = [a for a in argv if a != "--apply"]
    if len(argv) != 2:
        print(__doc__)
        sys.exit(1)
    race_id, source = int(argv[0]), argv[1]
    db = get_db()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    if not race:
        print("race not found")
        sys.exit(1)
    if os.path.exists(source):
        rows = results.parse_csv(open(source).read())
        label = os.path.basename(source)
    else:
        kind, event, race_no = results.parse_source(source)
        rows = results.fetch_yachtscoring(event, race_no)
        label = f"yachtscoring:{event}#{race_no}"
    matches, unmatched, roster_left = results.match_roster(db, race_id, rows)
    print(f"{race['name']}: {len(rows)} result rows, {len(matches)} matched")
    for rb, res in matches:
        print(f"  {rb['name']:<32} <- {res['name']:<32} {res['status']:<4} "
              f"{results.dt.datetime.fromtimestamp(res['finish_at'], results.dt.timezone.utc).strftime('%d %b %H:%M:%SZ') if res['finish_at'] else '—':<17}"
              f" elapsed {res['elapsed_s'] or '—'}  class {res['klass']}")
    if unmatched:
        print(f"  results with no roster boat ({len(unmatched)}):")
        for r in unmatched:
            print(f"    {r['name']} {r['sail_no'] or ''} {r['status']}")
    if roster_left:
        print(f"  roster boats with no result ({len(roster_left)}):")
        for rb in roster_left:
            print(f"    {rb['name']}")
    if not apply:
        print("preview only; add --apply to write these")
        return
    summary = results.apply_results(db, race, matches, label)
    print(f"applied: {summary}")


if __name__ == "__main__":
    main()
