"""The AIS feed reads aisstream.io envelopes the way they are actually sent.

aisstream's MetaData spells the position 'latitude' / 'longitude' (lower
case); the typed body under Message spells it 'Latitude' / 'Longitude'.
Reading only the capitalised form from MetaData dropped every report.
"""
import time

from tests.conftest import make_race
from vn import ais

COWS = (41.00359, -73.52394)


def envelope(mmsi, name, lat, lon, t, meta_case="lower", body=True, sog=6.1):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) + ".123456789 +0000 UTC"
    meta = {"MMSI": mmsi, "MMSI_String": mmsi, "ShipName": name, "time_utc": stamp}
    if meta_case == "lower":
        meta.update(latitude=lat, longitude=lon)
    elif meta_case == "upper":
        meta.update(Latitude=lat, Longitude=lon)
    report = {"MessageID": 1, "UserID": mmsi, "Sog": sog, "Cog": 80.0, "Valid": True}
    if body:
        report.update(Latitude=lat, Longitude=lon)
    return {"MessageType": "PositionReport", "MetaData": meta,
            "Message": {"PositionReport": report}}


def vineyard(db):
    race_id = make_race(db, [("Cows", *COWS), ("BB Tower", 41.3967, -71.0333),
                             ("Block Island", 41.1534, -71.5521),
                             ("Finish", 41.0148, -73.5375)],
                        start_time=int(time.time()) - 3600)
    db.execute("UPDATE races SET ais=1 WHERE id=?", (race_id,))
    db.execute("INSERT INTO real_boats(race_id,name) VALUES (?,?)", (race_id, "Moneyball"))
    db.commit()
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    feed = ais.AISFeed("key")
    feed._load_roster(db, race_id)
    return race, {race_id: ais.get_marks(db, race_id)}, feed


def boat(db, race):
    return db.execute("SELECT * FROM real_boats WHERE race_id=?", (race["id"],)).fetchone()


def test_real_envelope_binds_the_boat_and_stores_the_point(db):
    race, marks, feed = vineyard(db)
    t = int(time.time()) - 60
    msg = envelope(367123456, "MONEYBALL@@@@@@@@@@@", 41.01, -73.40, t)
    assert feed._handle(db, msg, [race], marks) == 1
    rb = boat(db, race)
    assert rb["mmsi"] == 367123456
    assert (rb["last_t"], rb["last_lat"], rb["last_lon"]) == (t, 41.01, -73.40)
    assert db.execute("SELECT COUNT(*) c FROM real_track WHERE rb_id=?",
                      (rb["id"],)).fetchone()["c"] == 1
    log = db.execute("SELECT message FROM race_log ORDER BY id DESC").fetchone()["message"]
    assert "identified as MMSI 367123456" in log


def test_capitalised_metadata_still_read(db):
    race, marks, feed = vineyard(db)
    msg = envelope(367123456, "MONEYBALL", 41.01, -73.40, int(time.time()) - 60,
                   meta_case="upper", body=False)
    assert feed._handle(db, msg, [race], marks) == 1


def test_body_position_when_metadata_has_none(db):
    race, marks, feed = vineyard(db)
    msg = envelope(367123456, "MONEYBALL", 41.01, -73.40, int(time.time()) - 60,
                   meta_case="none")
    assert feed._handle(db, msg, [race], marks) == 1


def test_no_position_anywhere_is_dropped(db):
    race, marks, feed = vineyard(db)
    msg = envelope(367123456, "MONEYBALL", 41.01, -73.40, int(time.time()) - 60,
                   meta_case="none", body=False)
    assert feed._handle(db, msg, [race], marks) == 0
    assert boat(db, race)["mmsi"] is None


def test_report_outside_the_course_box_is_ignored(db):
    race, marks, feed = vineyard(db)
    msg = envelope(367123456, "MONEYBALL", 45.0, -60.0, int(time.time()) - 60)
    assert feed._handle(db, msg, [race], marks) == 0
    assert boat(db, race)["mmsi"] is None


def test_not_available_position_is_dropped(db):
    race, marks, feed = vineyard(db)
    msg = envelope(367123456, "MONEYBALL", 91.0, 181.0, int(time.time()) - 60)
    assert feed._handle(db, msg, [race], marks) == 0


# ---- binding gates: a name alone is not enough ---------------------------

def test_moored_vessel_with_a_roster_name_is_not_bound(db):
    race, marks, feed = vineyard(db)
    msg = envelope(367123456, "MONEYBALL", 41.05, -73.45, int(time.time()) - 60, sog=0.0)
    assert feed._handle(db, msg, [race], marks) == 0
    assert boat(db, race)["mmsi"] is None
    assert boat(db, race)["last_t"] is None


def test_vessel_without_a_speed_is_not_bound(db):
    race, marks, feed = vineyard(db)
    msg = envelope(367123456, "MONEYBALL", 41.05, -73.45, int(time.time()) - 60, sog=102.3)
    assert feed._handle(db, msg, [race], marks) == 0
    assert boat(db, race)["mmsi"] is None


def test_vessel_under_way_far_from_the_course_is_not_bound(db):
    race, marks, feed = vineyard(db)
    # New Bedford harbour: inside the box, 15 nm from the nearest leg
    msg = envelope(367123456, "MONEYBALL", 41.6386, -70.9185, int(time.time()) - 60, sog=5.0)
    assert feed._handle(db, msg, [race], marks) == 0
    assert boat(db, race)["mmsi"] is None


def test_bound_boat_is_followed_anywhere_in_the_box(db):
    race, marks, feed = vineyard(db)
    db.execute("UPDATE real_boats SET mmsi=367123456 WHERE race_id=?", (race["id"],))
    db.commit()
    feed._load_roster(db, race["id"])
    # drifting, well off the rhumb line: still her, still stored
    msg = envelope(367123456, "MONEYBALL", 41.6386, -70.9185, int(time.time()) - 60, sog=0.0)
    assert feed._handle(db, msg, [race], marks) == 1


def test_course_distance():
    marks = [{"lat": 41.0, "lon": -73.5}, {"lat": 41.0, "lon": -71.0}]
    assert abs(ais.course_distance_nm(41.0, -72.0, marks)) < 0.01        # on the leg
    assert abs(ais.course_distance_nm(41.1, -72.0, marks) - 6.0) < 0.1   # 6' north of it
    assert abs(ais.course_distance_nm(41.0, -70.5, marks) - 22.6) < 0.5  # beyond the end


def test_set_mmsi_wipe_clears_binding_and_track(db):
    race, marks, feed = vineyard(db)
    t = int(time.time()) - 60
    assert feed._handle(db, envelope(367123456, "MONEYBALL", 41.01, -73.40, t), [race], marks) == 1
    import importlib
    set_mmsi = importlib.import_module("scripts.set_mmsi")
    set_mmsi.main([str(race["id"]), "Moneyball", "none", "--wipe"])
    rb = boat(db, race)
    assert rb["mmsi"] is None and rb["last_t"] is None and rb["next_mark"] == 1
    assert db.execute("SELECT COUNT(*) c FROM real_track WHERE rb_id=?",
                      (rb["id"],)).fetchone()["c"] == 0


# ---- the fix time -------------------------------------------------------

def test_fix_time_uses_the_report_second_not_the_receipt_time():
    meta = {"time_utc": "2026-09-04 16:02:56.123456789 +0000 UTC"}   # received ashore
    t_recv = ais.parse_time(meta["time_utc"])
    assert ais.fix_time(meta, {"Timestamp": 50}) == t_recv - 6        # same minute
    assert ais.fix_time(meta, {"Timestamp": 5}) == t_recv - 51        # previous minute, not the future
    assert ais.fix_time(meta, {"Timestamp": 57}) == t_recv + 1        # a second of clock skew is fine
    assert ais.fix_time(meta, {"Timestamp": 60}) == t_recv            # 60+: second unavailable
    assert ais.fix_time(meta, {"Timestamp": None}) == t_recv
    assert ais.fix_time(meta, {}) == t_recv
    assert ais.fix_time({}, {"Timestamp": 10}, now=1000) == 1000        # no receipt time at all


def test_stored_fix_carries_the_report_second(db):
    race, marks, feed = vineyard(db)
    t = int(time.time()) - 300
    env = envelope(368000001, "MONEYBALL", 41.01, -73.40, t)
    env["Message"]["PositionReport"]["Timestamp"] = (t % 60 + 30) % 60   # half a minute off the receipt
    feed._handle(db, env, [race], marks)
    stored = db.execute("SELECT t FROM real_track WHERE rb_id=?", (boat(db, race)["id"],)).fetchone()["t"]
    assert stored == ais.fix_time(env["MetaData"], env["Message"]["PositionReport"])
    assert stored != t and stored <= t + 2
