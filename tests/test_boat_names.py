"""A virtual boat may not wear a real competitor's name, and an admin can
remove a boat whose name breaks the rules."""
from tests.conftest import new_client
from tests.test_api import make_race_via_api
from vn.db import delete_boat


def _real(db, race, name):
    db.execute("INSERT INTO real_boats(race_id, name, klass) VALUES (?,?,?)", (race, name, "IMOCA"))
    db.commit()


def test_real_boat_names_are_reserved(client, db):
    admin, race = make_race_via_api()
    _real(db, race, "Malizia 4")
    _real(db, race, "Midnight Rider - PMP Strategy")
    nav = new_client("nav")
    for bad in ("Malizia 4", "MALIZIA 4", "malizia-4", "Midnight Rider", "midnight rider"):
        r = nav.post(f"/api/races/{race}/boats", json={"name": bad})
        assert r.status_code == 409, (bad, r.get_json())
        assert "real boat" in r.get_json()["error"]
    ok = nav.post(f"/api/races/{race}/boats", json={"name": "Malizia Fan Club"})
    assert ok.status_code == 200, ok.get_json()


def test_name_is_trimmed_and_capped(client, db):
    admin, race = make_race_via_api()
    nav = new_client("nav")
    assert nav.post(f"/api/races/{race}/boats", json={"name": "x" * 41}).status_code == 400
    r = nav.post(f"/api/races/{race}/boats", json={"name": "  Sea   Bird  "})
    assert r.status_code == 200
    state = client.get(f"/api/races/{race}/state").get_json()
    assert [e["name"] for e in state["entries"]] == ["Sea Bird"]


def test_admin_removal_clears_the_boat_and_logs_it(client, db):
    admin, race = make_race_via_api()
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Rude Name"}).get_json()["boat_id"]
    nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    assert delete_boat(db, boat, "removed by the admin (offensive name)") == "Rude Name"
    db.commit()
    assert db.execute("SELECT 1 FROM boats WHERE id=?", (boat,)).fetchone() is None
    for t in ("route_wps", "route_log", "track"):
        assert db.execute(f"SELECT COUNT(*) c FROM {t} WHERE boat_id=?", (boat,)).fetchone()["c"] == 0
    log = client.get(f"/api/races/{race}/log").get_json()
    assert any(e["message"] == "Rude Name removed by the admin (offensive name)." for e in log)
    # the navigator still has an account and can enter again
    assert nav.get("/api/auth/me").get_json()["user"]["username"] == "nav"
    assert nav.post(f"/api/races/{race}/boats", json={"name": "Better Name"}).status_code == 200
    assert delete_boat(db, 9999, "x") is None
