"""Purge fallback weather from the caches so real data is refetched.

    .venv/bin/python scripts/heal_weather.py

On Fly:  fly ssh console -C "python /app/scripts/heal_weather.py"

A failed Open-Meteo fetch (rate limit, outage) caches synthetic wind /
zero current for the whole ±7-day window, and boats then sail fake
weather. get_wind/get_current retry such cells on a cooldown since the
fix in vn/wind.py, but this clears the backlog at once: fallback rows
are deleted and refetch lazily on next access. Boats that sailed fake
wind still carry it in their tracks — restart_boat replays them clean.
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
