"""Course reconciliation (vn.sim.enforce_course).

Geometry is set around the equator so 0.1° of longitude is ~6 nm and marks
can be placed by eye.
"""
from vn.geo import haversine_nm
from vn.sim import enforce_course

R = 2.0   # mark radius in nm


def mk(name, lat, lon, side=None):
    return {"name": name, "lat": lat, "lon": lon, "side": side}


START, FINISH = mk("Start", 0.0, 0.0), mk("Finish", 0.0, 1.0)


def test_route_through_every_mark_is_untouched():
    wps = [(0.0, 0.5), (0.0, 1.0)]
    out, notes = enforce_course(wps, [START, FINISH], 1, R)
    assert out == wps and notes == []


def test_missed_mark_is_inserted_at_closest_approach():
    turn = mk("Turn", 0.2, 0.5)                       # 12 nm north of the line
    wps = [(0.0, 0.5), (0.0, 1.0)]
    out, notes = enforce_course(wps, [START, turn, FINISH], 1, R)
    assert (0.2, 0.5) in out
    assert out.index((0.2, 0.5)) == 1
    assert any("misses Turn" in n for n in notes)


def test_unreached_finish_is_appended():
    out, notes = enforce_course([(0.0, 0.3)], [START, FINISH], 1, R)
    assert out[-1] == (0.0, 1.0)
    assert any("misses Finish" in n for n in notes)
    # and with no waypoints at all the finish is still appended
    out, notes = enforce_course([], [START, FINISH], 1, R)
    assert out == [(0.0, 1.0)]
    assert any("does not reach Finish" in n for n in notes)


def test_leading_waypoints_underfoot_are_skipped():
    out, notes = enforce_course([(0.001, 0.0), (0.0, 1.0)], [START, FINISH], 1, R,
                                start_pos=(0.0, 0.0))
    assert out == [(0.0, 1.0)]
    assert len(notes) == 1 and "1" in notes[0]          # reported, one way or another
    # without a boat position nothing is dropped
    out, notes = enforce_course([(0.001, 0.0), (0.0, 1.0)], [START, FINISH], 1, R)
    assert out == [(0.001, 0.0), (0.0, 1.0)] and notes == []


def test_resubmission_joins_ahead_of_the_boat():
    # the router re-ran from the start; the boat is already 30 nm along
    wps = [(0.0, 0.1), (0.0, 0.3), (0.0, 0.6), (0.0, 1.0)]
    out, notes = enforce_course(wps, [START, FINISH], 1, R, start_pos=(0.0, 0.5))
    assert out == [(0.0, 0.6), (0.0, 1.0)]
    assert any("joined" in n for n in notes)


def test_correct_side_pass_is_left_alone():
    # leave the mark to starboard while sailing east: pass north of it
    turn = mk("Turn", 0.0, 0.5, "stbd")
    wps = [(0.02, 0.4), (0.02, 0.5), (0.02, 0.6), (0.0, 1.0)]
    out, notes = enforce_course(wps, [START, turn, FINISH], 1, R)
    assert out == wps and notes == []


def test_wrong_side_pass_is_rebuilt_as_a_rounding():
    turn = mk("Turn", 0.0, 0.5, "stbd")
    wps = [(-0.02, 0.4), (-0.02, 0.5), (-0.02, 0.6), (0.0, 1.0)]
    out, notes = enforce_course(wps, [START, turn, FINISH], 1, R)
    assert any("does not leave Turn to starboard" in n for n in notes)
    assert len(out) > len(wps)
    # the rebuilt pass stays close to the mark, on the north side
    near = [p for p in out if haversine_nm(p[0], p[1], 0.0, 0.5) <= R]
    assert near and max(p[0] for p in near) > 0.01
    assert sum(p[0] for p in near) / len(near) > 0
    assert out[-1] == (0.0, 1.0)


def test_leg_straight_over_a_sided_mark_is_rebuilt():
    turn = mk("Turn", 0.0, 0.5, "port")
    wps = [(0.0, 0.4), (0.0, 0.5), (0.0, 0.6), (0.0, 1.0)]
    out, notes = enforce_course(wps, [START, turn, FINISH], 1, R)
    assert any("does not leave Turn to port" in n for n in notes)
    assert (0.0, 0.5) not in out


def test_missed_sided_mark_gets_a_rounding_on_the_right_side():
    turn = mk("Turn", 0.2, 0.5, "port")
    wps = [(0.0, 0.4), (0.0, 0.6), (0.0, 1.0)]
    out, notes = enforce_course(wps, [START, turn, FINISH], 1, R)
    assert any("leaving it to port" in n for n in notes)
    assert any(haversine_nm(p[0], p[1], 0.2, 0.5) <= R for p in out)
