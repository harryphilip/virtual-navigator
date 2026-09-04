import pytest

from vn.gpx import parse_coord, parse_route, parse_track, route_to_gpx, track_to_gpx

GPX_ROUTE = """<?xml version="1.0"?>
<gpx version="1.1" creator="qtVlm" xmlns="http://www.topografix.com/GPX/1/1">
  <rte><name>Test</name>
    <rtept lat="41.1" lon="-71.5"><name>A</name></rtept>
    <rtept lat="40.9" lon="-71.2"><name>B</name></rtept>
  </rte>
</gpx>"""

GPX_TRACK = """<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="41.1" lon="-71.5"><time>2026-09-01T12:00:00Z</time></trkpt>
    <trkpt lat="41.0" lon="-71.4"><time>2026-09-01T13:00:00Z</time></trkpt>
    <trkpt lat="40.9" lon="-71.3"></trkpt>
  </trkseg></trk>
</gpx>"""


def test_gpx_route_points():
    assert parse_route(GPX_ROUTE) == [(41.1, -71.5), (40.9, -71.2)]


def test_gpx_track_keeps_only_timed_points():
    pts = parse_track(GPX_TRACK)
    assert len(pts) == 2
    assert pts[0] == (1788264000, 41.1, -71.5)        # 2026-09-01T12:00Z


def test_gpx_route_prefers_rtept_over_wpt():
    text = GPX_ROUTE.replace("</gpx>", '<wpt lat="0" lon="0"/></gpx>')
    assert parse_route(text) == [(41.1, -71.5), (40.9, -71.2)]


def test_csv_with_header():
    text = "name,Latitude,Longitude\nA,41.1,-71.5\nB,40.9,-71.2\n"
    assert parse_route(text) == [(41.1, -71.5), (40.9, -71.2)]


def test_csv_bare_pairs_and_bom():
    text = "﻿41.1,-71.5\n40.9,-71.2\n"
    assert parse_route(text) == [(41.1, -71.5), (40.9, -71.2)]


def test_csv_semicolon_and_degrees_minutes():
    text = "lat;lon\n41 06.0 N;071 30.0 W\n"
    assert parse_route(text) == [(pytest.approx(41.1), pytest.approx(-71.5))]


def test_csv_track_time_lat_lon():
    text = "time,lat,lon\n2026-09-01T12:00:00Z,41.1,-71.5\n"
    assert parse_track(text) == [(1788264000, 41.1, -71.5)]


@pytest.mark.parametrize("text,value", [
    ("-71.5782", -71.5782),
    ("41.1754° N", 41.1754),
    ("41° 27.20' N", 41 + 27.2 / 60),
    ("071 21.40 W", -(71 + 21.4 / 60)),
    ("41°27'12\" S", -(41 + 27 / 60 + 12 / 3600)),
    ("41,5", 41.5),
    (41.25, 41.25),
])
def test_parse_coord_forms(text, value):
    assert parse_coord(text) == pytest.approx(value)


def test_parse_coord_rejects_junk():
    with pytest.raises(ValueError):
        parse_coord("north of the island")


def test_route_gpx_round_trip_and_escaping():
    marks = [{"name": "Start & <finish>", "lat": 41.1, "lon": -71.5},
             {"name": "B", "lat": 40.9, "lon": -71.2}]
    text = route_to_gpx('Race "one"', marks, desc="d")
    assert parse_route(text) == [(41.1, -71.5), (40.9, -71.2)]
    assert "&amp;" in text and "&lt;finish" in text and "&quot;" in text


def test_track_gpx_round_trip():
    text = track_to_gpx("T", [(1788264000, 41.1, -71.5), (1788267600, 41.0, -71.4)])
    assert parse_track(text) == [(1788264000, 41.1, -71.5), (1788267600, 41.0, -71.4)]
