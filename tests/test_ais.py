"""The AIS feed reads aisstream.io envelopes the way they are actually sent.

aisstream's MetaData spells the position 'latitude' / 'longitude' (lower
case); the typed body under Message spells it 'Latitude' / 'Longitude'.
Reading only the capitalised form from MetaData dropped every report.
"""
import time

from tests.conftest import make_race
from vn import ais

COWS = (41.00359, -73.52394)


def envelope(mmsi, name, lat, lon, t, meta_case="lower", body=True):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) + ".123456789 +0000 UTC"
    meta = {"MMSI": mmsi, "MMSI_String": mmsi, "ShipName": name, "time_utc": stamp}
    if meta_case == "lower":
        meta.update(latitude=lat, longitude=lon)
    elif meta_case == "upper":
        meta.update(Latitude=lat, Longitude=lon)
    report = {"MessageID": 1, "UserID": mmsi, "Sog": 6.1, "Cog": 80.0, "Valid": True}
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
