"""Should this commit go to production right now?  Run by the Action.

    python scripts/deploy_guard.py --overview https://virtual-navigator.com/api/overview \
        --last-release 2026-09-06T18:16:25Z [--force]

Prints the decision and exits 0 (deploy) or 3 (hold). Two rules, both
from the working agreements in CLAUDE.md, now enforced instead of
remembered:

  freeze   From FREEZE_BEFORE_H hours before any real race's gun until
           FREEZE_AFTER_H after it, nothing goes to production: a deploy
           restarts the engine and replays the gap under the lock, and the
           start is the worst moment for that.
  cadence  While a real race is under way, at most one production deploy
           per MIN_GAP_H hours, so the fleet is not restarted five times
           in an afternoon.

The practice course (a rolling start) never holds a deploy. --force, set
by "[deploy]" in the commit message or the workflow's force input, is a
human decision and overrides both rules; the reason is still printed.
Staging is deployed regardless, so a held commit is still smoke-tested.
"""
import argparse
import datetime as dt
import json
import sys
import urllib.request

FREEZE_BEFORE_H = 2
FREEZE_AFTER_H = 1
MIN_GAP_H = 20


def decide(now, races, last_release_at, force=False):
    """(go, reason). now and last_release_at are unix seconds (the latter
    None when unknown); races are /api/overview rows."""
    real = [r for r in races if not r.get("rolling")]
    for r in real:
        gun = r["start_time"]
        if gun - FREEZE_BEFORE_H * 3600 <= now <= gun + FREEZE_AFTER_H * 3600:
            when = "starts" if gun > now else "started"
            reason = (f"freeze: {r['name']} {when} at "
                      f"{dt.datetime.fromtimestamp(gun, dt.timezone.utc):%d %b %H:%MZ}; no production "
                      f"deploys from {FREEZE_BEFORE_H} h before to {FREEZE_AFTER_H} h after a gun")
            return (True, reason + " (forced)") if force else (False, reason)
    racing = [r["name"] for r in real if r.get("status") == "racing"]
    if racing and last_release_at is not None:
        gap_h = (now - last_release_at) / 3600
        if gap_h < MIN_GAP_H:
            reason = (f"cadence: {', '.join(racing)} under way and the last production "
                      f"release was {gap_h:.1f} h ago (minimum {MIN_GAP_H} h between deploys "
                      "while a race runs)")
            return (True, reason + " (forced)") if force else (False, reason)
    if racing:
        return True, f"{', '.join(racing)} under way; last release long enough ago"
    return True, "no real race under way or about to start"


def parse_iso(s):
    s = s.strip().replace("Z", "+00:00")
    return int(dt.datetime.fromisoformat(s).timestamp())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--overview", required=True, help="URL of /api/overview on production")
    ap.add_argument("--last-release", help="ISO time of the last production release")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        with urllib.request.urlopen(a.overview, timeout=30) as resp:
            races = json.loads(resp.read().decode())
    except Exception as e:                       # noqa: BLE001 - the guard must not block on a blip
        print(f"deploy_guard: could not read {a.overview} ({e}); deploying")
        sys.exit(0)
    last = parse_iso(a.last_release) if a.last_release else None
    go, reason = decide(int(dt.datetime.now(dt.timezone.utc).timestamp()), races, last, a.force)
    print(f"deploy_guard: {'deploy' if go else 'hold'} — {reason}")
    sys.exit(0 if go else 3)


if __name__ == "__main__":
    main()
