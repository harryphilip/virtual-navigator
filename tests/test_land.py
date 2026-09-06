"""Land crossings: the point test against the shipped polygons, the leg
sampler, and the route endpoint's refuse-or-warn rule."""
import pytest

from tests.conftest import new_client
from tests.test_api import make_race_via_api
from vn import land


def test_known_points():
    assert land.available()
    assert land.is_land(38.12, 13.36)          # Palermo
    assert land.is_land(52.52, 13.40)          # Berlin
    assert land.is_land(32.30, -64.78)         # Bermuda's main island
    assert not land.is_land(36.50, 15.90)      # off Capo Passero
    assert not land.is_land(40.00, -60.00)     # mid-Atlantic
    assert not land.is_land(0.0, 0.0)          # Gulf of Guinea
    assert not land.is_land(41.40, -71.35)     # off Brenton Reef, the practice start


def test_leg_across_sicily_is_mostly_land():
    nm, first = land.leg_land_nm((37.0, 12.5), (38.5, 15.5))     # Marsala side to Messina side
    assert nm > 60 and first is not None


def test_leg_at_sea_is_clean():
    nm, first = land.leg_land_nm((41.40, -71.35), (32.42, -64.60))   # Newport to Bermuda
    assert nm == 0 and first is None


def test_long_crossing_is_refused_short_one_warned(client, db, monkeypatch):
    admin, race = make_race_via_api()            # 0,0 to 0.5S, at sea
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]

    # real geography: a waypoint near Benin City drags the leg ~100 nm over Nigeria
    r = nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[6.5, 6.0], [-0.5, 0.0]]})
    assert r.status_code == 400
    assert "sails over land" in r.get_json()["error"] and "leg 1" in r.get_json()["error"]

    # a synthetic islet 2 nm wide on the way: allowed, with a warning
    monkeypatch.setattr(land, "is_land", lambda lat, lon: -0.27 <= lat <= -0.24 and abs(lon) < 0.05)
    r = nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    assert r.status_code == 200, r.get_json()
    warn = [a for a in r.get_json()["adjustments"] if a.startswith("Warning") and "land" in a]
    assert len(warn) == 1 and "half speed" in warn[0]

    # open water: no note at all
    monkeypatch.setattr(land, "is_land", lambda lat, lon: False)
    r = nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    assert r.status_code == 200
    assert not [a for a in r.get_json()["adjustments"] if "land" in a]


def test_describe_names_the_leg():
    c = {"leg": 3, "from": (41.2, -71.3), "to": (40.9, -70.1), "land_nm": 12.0, "at": (41.1, -71.0)}
    assert land.describe(c) == ("leg 3 (41.20N 71.30W to 40.90N 70.10W) crosses about 12 nm "
                                "of land near 41.10N 71.00W")
