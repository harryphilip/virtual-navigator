import pytest

from vn.geo import (angle_diff, bearing_deg, destination, haversine_nm,
                    point_in_poly)


def test_one_degree_of_latitude_is_sixty_miles():
    assert haversine_nm(40, -70, 41, -70) == pytest.approx(60.0, rel=0.002)


def test_bearings_cardinal():
    assert bearing_deg(0, 0, 1, 0) == pytest.approx(0)
    assert bearing_deg(0, 0, 0, 1) == pytest.approx(90)
    assert bearing_deg(0, 0, -1, 0) == pytest.approx(180)
    assert bearing_deg(0, 0, 0, -1) == pytest.approx(270)


def test_destination_round_trips():
    lat, lon = destination(41.0, -71.0, 135, 25)
    assert haversine_nm(41.0, -71.0, lat, lon) == pytest.approx(25, rel=1e-6)
    assert bearing_deg(41.0, -71.0, lat, lon) == pytest.approx(135, abs=0.01)


def test_destination_wraps_the_antimeridian():
    lat, lon = destination(0, 179.9, 90, 30)
    assert -180 <= lon <= 180
    assert lon < 0


def test_angle_diff_is_smallest_arc():
    assert angle_diff(350, 10) == 20
    assert angle_diff(10, 350) == 20
    assert angle_diff(0, 180) == 180
    assert angle_diff(90, 90) == 0


def test_point_in_poly():
    box = [(0, 0), (0, 1), (1, 1), (1, 0)]
    assert point_in_poly(0.5, 0.5, box)
    assert not point_in_poly(1.5, 0.5, box)
    assert not point_in_poly(0.5, -0.1, box)
