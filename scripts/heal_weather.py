"""Purge fallback weather from the caches so real data is refetched.

    .venv/bin/python scripts/heal_weather.py

On Fly:  fly ssh console -C "python /app/scripts/heal_weather.py"

A failed Open-Meteo fetch (rate limit, outage) caches synthetic wind /
zero current for a short window, and boats sail that placeholder until
it heals. The ticker refetches such cells itself every 15 minutes
(vn.wind.heal_fallback), so this script is only the manual backstop:
it deletes every fallback row at once so real data is refetched on next
access. Boats that sailed fake wind still carry it in their tracks —
restart_boat replays them clean.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn.db import get_db


def main():
    db = get_db()
    w = db.execute("DELETE FROM wind_cache WHERE source='synthetic'").rowcount
    c = db.execute("DELETE FROM current_cache WHERE source='none'").rowcount
    db.commit()
    print(f"purged {w} synthetic wind row(s), {c} zero-current row(s) — "
          "real data refetches on next access")


if __name__ == "__main__":
    main()
