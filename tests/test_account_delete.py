"""A navigator can delete their own account, and everything it owns goes
with it; the last admin cannot."""
from tests.conftest import new_client
from tests.test_api import make_race_via_api


def test_delete_own_account_removes_boats_and_sessions(client, db):
    admin, race = make_race_via_api()
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    uid = db.execute("SELECT id FROM users WHERE username='nav'").fetchone()["id"]
    assert db.execute("SELECT COUNT(*) c FROM route_log WHERE boat_id=?", (boat,)).fetchone()["c"] >= 1

    assert nav.post("/api/auth/delete", json={"password": "wrong"}).status_code == 403
    r = nav.post("/api/auth/delete", json={"password": "secret1"})
    assert r.status_code == 200

    assert db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is None
    assert db.execute("SELECT COUNT(*) c FROM sessions WHERE user_id=?", (uid,)).fetchone()["c"] == 0
    assert db.execute("SELECT 1 FROM boats WHERE id=?", (boat,)).fetchone() is None
    for t in ("route_wps", "route_log", "track"):
        assert db.execute(f"SELECT COUNT(*) c FROM {t} WHERE boat_id=?", (boat,)).fetchone()["c"] == 0
    # signed out, and the profile is gone
    assert nav.get("/api/auth/me").get_json()["user"] is None
    assert client.get("/api/users/nav").status_code == 404
    # the race noted the withdrawal
    log = client.get(f"/api/races/{race}/log").get_json()
    assert any("Magpie" in e["message"] and "withdrawn" in e["message"] for e in log)
    # and the leaderboard no longer lists the boat
    names = [e["name"] for e in client.get(f"/api/races/{race}/state").get_json()["entries"]]
    assert "Magpie" not in names


def test_last_admin_cannot_delete_itself(client, db):
    admin = new_client("admin")          # first account on a fresh server
    r = admin.post("/api/auth/delete", json={"password": "secret1"})
    assert r.status_code == 409
    assert db.execute("SELECT 1 FROM users WHERE username='admin'").fetchone() is not None


def test_signed_out_cannot_delete(client):
    assert client.post("/api/auth/delete", json={"password": "x"}).status_code == 401


def test_privacy_and_terms_pages_are_served(client):
    for path in ("/privacy", "/terms"):
        r = client.get(path)
        assert r.status_code == 200
        assert b"Virtual Navigator" in r.data
