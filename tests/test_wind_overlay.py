"""The chart's wind overlay: the latest forecast snapshot, decoded back
from its GRIB into a grid of u/v in knots."""
import math

import vn.forecast as forecast
from tests.conftest import make_race

MARKS = [("Start", 41.0, -71.0), ("Finish", 41.5, -70.0)]


def _steady_series(spd_ms, from_deg):
    """Every grid point sees the same wind at every hour of the run."""
    def fetch(points):
        issued = forecast.time.time() // 3600 * 3600
        hours = {int(issued + h * 3600): (spd_ms, from_deg) for h in range(0, 121)}
        return [dict(hours) for _ in points]
    return fetch


def test_field_is_the_latest_snapshot_in_knots(client, db, monkeypatch):
    race_id = make_race(db, MARKS)
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    # 10 m/s from the west: flows east, so u = +19.4 kn, v = 0
    monkeypatch.setattr(forecast, "_fetch_batches", _steady_series(10.0, 270.0))
    forecast.make_snapshot(db, race)
    # a second, newer snapshot from the north must be the one served
    monkeypatch.setattr(forecast, "_fetch_batches", _steady_series(5.0, 0.0))
    newest = forecast.make_snapshot(db, race)
    db.execute("UPDATE forecast_snapshots SET issued_at = issued_at + 3600 WHERE id=?",
               (newest,))
    db.commit()

    r = client.get(f"/api/races/{race_id}/wind")
    assert r.status_code == 200
    f = r.get_json()
    assert f["id"] == newest
    la1, lo1, step, ni, nj = forecast.grid_for_race(forecast.race_points(db, race_id))
    assert (f["la1"], f["lo1"], f["step"], f["ni"], f["nj"]) == (la1, lo1, step, ni, nj)
    assert [fr["fh"] for fr in f["frames"]] == forecast.FORECAST_HOURS
    for fr in f["frames"]:
        assert fr["t"] == f["issued_at"] + fr["fh"] * 3600
        assert len(fr["u"]) == len(fr["v"]) == ni * nj
        # 5 m/s from due north flows south: u = 0, v = -9.7 kn
        assert all(abs(u) < 0.15 for u in fr["u"])
        assert all(abs(v + 5.0 * forecast.MS_TO_KN) < 0.15 for v in fr["v"])


def test_components_follow_the_meteorological_convention(client, db, monkeypatch):
    race_id = make_race(db, MARKS)
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    monkeypatch.setattr(forecast, "_fetch_batches", _steady_series(10.0, 270.0))
    forecast.make_snapshot(db, race)
    f = client.get(f"/api/races/{race_id}/wind").get_json()
    u, v = f["frames"][0]["u"][0], f["frames"][0]["v"][0]
    assert abs(u - 10.0 * forecast.MS_TO_KN) < 0.15 and abs(v) < 0.15
    # and the chart's readout recovers "from 270°" from them
    frm = (math.degrees(math.atan2(u, v)) + 180.0) % 360.0
    assert abs(frm - 270.0) < 1.0


def test_no_snapshot_yet_is_null_not_an_error(client, db):
    race_id = make_race(db, MARKS)
    r = client.get(f"/api/races/{race_id}/wind")
    assert r.status_code == 200
    assert r.get_json() is None


def test_unknown_race_is_404(client):
    assert client.get("/api/races/999/wind").status_code == 404


def test_snapshot_grid_follows_the_fleet_not_just_the_marks(client, db, monkeypatch):
    """A transatlantic fleet sails the great circle, well north of the line
    between the marks: the grid must reach the boats, real and virtual."""
    from tests.conftest import make_boat
    race_id = make_race(db, [("New York", 39.64, -71.25), ("Lorient", 47.69, -3.42)])
    marks_only = forecast.grid_for_race(forecast.race_points(db, race_id))
    assert marks_only[0] == 47.69 + forecast.PAD_DEG          # northern edge

    make_boat(db, race_id, lat=52.0, lon=-30.0)                # virtual, far north
    db.execute("INSERT INTO real_boats(race_id,name,last_lat,last_lon,last_t) "
               "VALUES (?,?,?,?,?)", (race_id, "Malizia", 50.3, -27.5, 0))
    db.commit()
    la1, lo1, step, ni, nj = forecast.grid_for_race(forecast.race_points(db, race_id))
    assert la1 == 52.0 + forecast.PAD_DEG
    assert la1 - step * (nj - 1) <= 39.64 - forecast.PAD_DEG + step
    assert ni * nj <= forecast.MAX_POINTS

    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    monkeypatch.setattr(forecast, "_fetch_batches", _steady_series(8.0, 200.0))
    forecast.make_snapshot(db, race)
    f = client.get(f"/api/races/{race_id}/wind").get_json()
    assert f["la1"] == la1 and f["ni"] == ni and f["nj"] == nj


def test_grid_stays_under_the_point_cap():
    """A wide grid coarsens rather than grows: MAX_POINTS bounds the API calls."""
    la1, lo1, step, ni, nj = forecast.grid_for_race([(39.6, -71.3), (52.0, -3.4)])
    assert ni * nj <= forecast.MAX_POINTS
    assert step <= 2.0
