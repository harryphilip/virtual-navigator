"""The simulation engine, sailed through mocked weather on a temp database."""
import pytest

from vn.geo import destination, haversine_nm
from vn.sim import catch_up_race
from tests.conftest import (POLAR_40FT, POLAR_CLASS40, boat_row, make_boat,
                            make_race, set_route, track_rows)

H = 3600
# a north–south line: waypoints "south" are dead downwind of 15 kn northerlies
START = ("Start", 0.0, 0.0)
FINISH = ("Finish", -0.5, 0.0)          # 30 nm south


def test_boat_sails_to_waypoints_in_order_and_locks_them(db, weather):
    race = make_race(db, [START, FINISH])
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(-0.1, 0.0), (-0.2, 0.0)])    # 6 nm, then 12 nm
    catch_up_race(db, race, now=2 * H)
    b = boat_row(db, boat)
    assert b["sim_time"] == 2 * H
    assert b["lat"] < -0.1
    passed = [r["seq"] for r in db.execute(
        "SELECT seq FROM route_wps WHERE boat_id=? AND passed=1", (boat,))]
    assert passed == [0, 1]
    rows = track_rows(db, boat)
    assert len(rows) == 12                               # one row per 10-min step
    assert {r[7] for r in rows} == {"test"}              # wind source is on the track


def test_boat_with_no_route_parks(db, weather):
    race = make_race(db, [START, FINISH])
    boat = make_boat(db, race, started_at=0)
    catch_up_race(db, race, now=H)
    b = boat_row(db, boat)
    assert (b["lat"], b["lon"]) == (0.0, 0.0)
    assert b["sim_time"] == H
    assert all(r[5] == 0.0 for r in track_rows(db, boat))  # bsp zero


def test_finish_is_recorded_and_the_boat_stops(db, weather):
    race = make_race(db, [START, FINISH])
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(-0.5, 0.0)])
    catch_up_race(db, race, now=12 * H)
    b = boat_row(db, boat)
    assert b["finished_at"] is not None
    assert b["next_mark"] == 2
    assert b["finished_at"] < 8 * H                      # 30 nm downwind at ~7 kn
    rows = track_rows(db, boat)
    assert rows[-1][0] == b["finished_at"]               # nothing sailed after


def test_time_only_moves_forward(db, weather):
    race = make_race(db, [START, FINISH])
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(-0.5, 0.0)])
    catch_up_race(db, race, now=H)
    before = track_rows(db, boat)
    catch_up_race(db, race, now=H // 2)                  # earlier "now"
    assert track_rows(db, boat) == before
    assert boat_row(db, boat)["sim_time"] == H


def test_resubmission_never_rewrites_the_sailed_track(db, weather):
    race = make_race(db, [START, FINISH])
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(-0.5, 0.0)])
    catch_up_race(db, race, now=H)
    sailed = track_rows(db, boat)
    set_route(db, boat, [(-0.3, 0.1), (-0.5, 0.0)])      # new plan for the future
    catch_up_race(db, race, now=2 * H)
    after = track_rows(db, boat)
    assert after[:len(sailed)] == sailed
    assert len(after) > len(sailed)


def test_wind_shift_forces_a_tack_and_charges_the_penalty(db, weather):
    race = make_race(db, [START, ("Finish", 0.5, 0.0)], penalty_s=600)
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(0.5, 0.0)])                    # dead upwind
    # northerly for the first hour, then a 40° header
    weather.wind = lambda lat, lon, t: (0.0, 15.0) if t < H else (320.0, 15.0)
    catch_up_race(db, race, now=2 * H)
    b = boat_row(db, boat)
    assert b["maneuvers"] >= 1
    assert b["wind_side"] in (1, -1)
    # the same run with no penalty gets further: the tack cost real time
    race2 = make_race(db, [START, ("Finish", 0.5, 0.0)], penalty_s=0)
    boat2 = make_boat(db, race2, started_at=0)
    set_route(db, boat2, [(0.5, 0.0)])
    catch_up_race(db, race2, now=2 * H)
    assert boat_row(db, boat2)["lat"] > b["lat"]


def test_shallow_water_halves_speed_and_counts_steps(db, weather):
    deep = make_race(db, [START, FINISH])
    shoal = make_race(db, [START, FINISH])
    b_deep = make_boat(db, deep, started_at=0)
    b_shoal = make_boat(db, shoal, started_at=0)
    for b in (b_deep, b_shoal):
        set_route(db, b, [(-0.5, 0.0)])
    catch_up_race(db, deep, now=H)
    weather.depth_ft = lambda lat, lon: 8.0              # under the 15 ft limit
    catch_up_race(db, shoal, now=H)
    d_deep = -boat_row(db, b_deep)["lat"]
    d_shoal = -boat_row(db, b_shoal)["lat"]
    assert d_shoal == pytest.approx(d_deep / 2, rel=0.01)
    assert boat_row(db, b_shoal)["groundings"] == 6


def test_exclusion_zone_halves_speed_and_counts_steps(db, weather):
    zone = {"name": "TSS", "pts": [[0.1, -0.1], [0.1, 0.1], [-1.0, 0.1], [-1.0, -0.1]]}
    clear = make_race(db, [START, FINISH])
    zoned = make_race(db, [START, FINISH], zones=[zone])
    b_clear = make_boat(db, clear, started_at=0)
    b_zoned = make_boat(db, zoned, started_at=0)
    for b in (b_clear, b_zoned):
        set_route(db, b, [(-0.5, 0.0)])
    catch_up_race(db, clear, now=H)
    catch_up_race(db, zoned, now=H)
    assert -boat_row(db, b_zoned)["lat"] == pytest.approx(-boat_row(db, b_clear)["lat"] / 2, rel=0.01)
    assert boat_row(db, b_zoned)["zone_steps"] == 6


def test_current_sets_a_parked_boat(db, weather):
    race = make_race(db, [START, FINISH], currents=True)
    boat = make_boat(db, race, started_at=0)
    weather.current = lambda lat, lon, t: (90.0, 2.0)    # 2 kn setting east
    catch_up_race(db, race, now=H)
    b = boat_row(db, boat)
    assert haversine_nm(0, 0, b["lat"], b["lon"]) == pytest.approx(2.0, rel=0.01)
    assert b["lon"] > 0


def test_finished_boat_is_never_advanced_again(db, weather):
    race = make_race(db, [START, FINISH])
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(-0.5, 0.0)])
    catch_up_race(db, race, now=12 * H)
    rows = track_rows(db, boat)
    catch_up_race(db, race, now=24 * H)
    assert track_rows(db, boat) == rows


@pytest.mark.parametrize("polar,tws,step_nm,abeam_nm", [
    (POLAR_CLASS40, 25.0, 2.615, 1.8),      # Class40 broad-reaching: 2.6 nm a step
    (POLAR_40FT, 12.0, 1.21, 1.95),         # a cruiser-racer at 7 kn
], ids=["class40", "40ft"])
def test_every_mark_is_honoured_when_passed_abeam(db, weather, polar, tws, step_nm, abeam_nm):
    """A boat must never sail past a mark unrecorded (review finding C-1).

    A routing that passes a mark abeam — inside the radius, as course
    reconciliation accepts, but well off to one side — crosses the 2 nm
    circle on a chord shorter than one step, so both step ends fall outside
    it. Passage has to be judged along the path, not at step ends. Ten marks
    along one bearing, spaced so the passes land at different phases of a
    step.
    """
    weather.steady(0.0, tws)
    marks, route = [START], []
    lat, lon = 0.0, 0.0
    for i in range(10):
        lat, lon = destination(lat, lon, 135.0, 2 * step_nm + 0.3)
        marks.append((f"M{i + 1}", lat, lon))
        route.append(destination(lat, lon, 45.0, abeam_nm))
    route.append((lat, lon))                                   # finish itself
    race = make_race(db, marks, polar_text=polar, mark_radius_nm=2.0)
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, route)
    catch_up_race(db, race, now=12 * H)
    b = boat_row(db, boat)
    assert b["next_mark"] == len(marks), "a mark was sailed past without being recorded"
    assert b["finished_at"] is not None


def test_marks_are_credited_in_course_order_only(db, weather):
    """A boat that sails wide of a turning mark still owes it, however close
    it comes to the finish."""
    race = make_race(db, [START, ("Turn", -0.3, 0.2), FINISH], mark_radius_nm=2.0)
    boat = make_boat(db, race, started_at=0)
    set_route(db, boat, [(-0.5, 0.0)])                  # straight to the finish
    catch_up_race(db, race, now=12 * H)
    b = boat_row(db, boat)
    assert b["next_mark"] == 1 and b["finished_at"] is None
    # marks stacked on one spot are credited in sequence from a single pass
    race2 = make_race(db, [START, ("Turn", -0.5, 0.0), FINISH], mark_radius_nm=2.0)
    boat2 = make_boat(db, race2, started_at=0)
    set_route(db, boat2, [(-0.5, 0.0)])
    catch_up_race(db, race2, now=12 * H)
    b2 = boat_row(db, boat2)
    assert b2["next_mark"] == 3 and b2["finished_at"] is not None
