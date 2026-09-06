"""Rolling-start races (the practice course): enter after the gun, start
when you submit, rank finishers by elapsed time, and never run out of
editions."""
import os
import time

from tests.conftest import make_boat, new_client
from tests.test_api import make_race_via_api
from vn.practice import ensure_practice, open_practice

PRACTICE = os.path.join(os.path.dirname(__file__), "..", "data", "races",
                        "practice_newport_bermuda.json")


def _rolling_race(db, gun_ago=3600):
    admin, race = make_race_via_api(start_time=int(time.time()) - gun_ago)
    db.execute("UPDATE races SET rolling=1 WHERE id=?", (race,))
    db.commit()
    return admin, race


def test_entries_stay_open_after_the_gun_and_the_boat_starts_on_submission(client, db, weather):
    admin, race = _rolling_race(db)
    info = client.get(f"/api/races/{race}").get_json()
    assert info["rolling"] is True and info["entries_open"] is True
    assert info["entries_close_at"] == info["start_time"] + 28 * 86400

    nav = new_client("nav")
    r = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"})
    assert r.status_code == 200, r.get_json()
    boat = r.get_json()["boat_id"]
    # entered but no route: the boat waits on the dock, nobody gives it a course
    state = client.get(f"/api/races/{race}/state").get_json()
    me = [e for e in state["entries"] if e["name"] == "Magpie"][0]
    assert me["started"] is False and me["rank"] is None

    before = int(time.time())
    assert nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]}).status_code == 200
    row = db.execute("SELECT sim_time, started_at FROM boats WHERE id=?", (boat,)).fetchone()
    assert row["started_at"] is not None and before <= row["started_at"] <= int(time.time()) + 5
    state = client.get(f"/api/races/{race}/state").get_json()
    me = [e for e in state["entries"] if e["name"] == "Magpie"][0]
    assert me["started"] is True and me["started_at"] == row["started_at"]
    assert state["rolling"] is True


def test_finishers_rank_by_elapsed_not_by_finish_order(client, db):
    admin, race = _rolling_race(db, gun_ago=10 * 86400)
    now = int(time.time())
    # Early started 5 days ago and took 4 days; Late started 2 days ago and took 1 day
    early = make_boat(db, race, name="Early", started_at=now - 5 * 86400)
    late = make_boat(db, race, name="Late", started_at=now - 2 * 86400)
    db.execute("UPDATE boats SET started_at=sim_time, finished_at=? WHERE id=?", (now - 86400, early))
    db.execute("UPDATE boats SET started_at=sim_time, finished_at=? WHERE id=?", (now - 3600, late))
    db.commit()
    entries = client.get(f"/api/races/{race}/state").get_json()["entries"]
    by = {e["name"]: e for e in entries}
    assert by["Late"]["elapsed_s"] == 2 * 86400 - 3600
    assert by["Early"]["elapsed_s"] == 4 * 86400
    assert by["Late"]["rank"] == 1 and by["Early"]["rank"] == 2   # Late got home last, but faster


def test_gun_start_still_ranks_by_finish_order(client, db):
    admin, race = make_race_via_api(start_time=int(time.time()) - 10 * 86400)
    now = int(time.time())
    a = make_boat(db, race, name="A", started_at=now - 5 * 86400)
    b = make_boat(db, race, name="B", started_at=now - 5 * 86400)
    db.execute("UPDATE boats SET finished_at=? WHERE id=?", (now - 3600, a))
    db.execute("UPDATE boats SET finished_at=? WHERE id=?", (now - 7200, b))
    db.commit()
    by = {e["name"]: e for e in client.get(f"/api/races/{race}/state").get_json()["entries"]}
    assert by["B"]["rank"] == 1 and by["A"]["rank"] == 2


def test_practice_edition_opens_once_and_again_when_entries_close(db):
    now = int(time.time())
    rid = ensure_practice(db, now, path=PRACTICE)
    assert rid is not None
    r = db.execute("SELECT * FROM races WHERE id=?", (rid,)).fetchone()
    assert r["rolling"] == 1
    assert r["start_time"] == now - now % 3600
    assert r["name"].startswith("Practice: Newport to Bermuda")
    assert db.execute("SELECT COUNT(*) c FROM marks WHERE race_id=?", (rid,)).fetchone()["c"] == 2
    # still open: nothing more is created
    assert ensure_practice(db, now + 3600, path=PRACTICE) is None
    assert open_practice(db, now + 3600)["id"] == rid
    # entries closed: the next edition opens with a distinct name
    later = now + 29 * 86400
    rid2 = ensure_practice(db, later, path=PRACTICE)
    assert rid2 is not None and rid2 != rid
    names = {x["name"] for x in db.execute("SELECT name FROM races")}
    assert len(names) == 2
    assert open_practice(db, later)["id"] == rid2


def test_overview_features_the_practice_course(client, db):
    ensure_practice(db, int(time.time()), path=PRACTICE)
    ov = client.get("/api/overview").get_json()
    p = [r for r in ov if r["rolling"]][0]
    assert p["status"] == "racing" and p["entries_open"] is True
