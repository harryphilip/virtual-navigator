"""Rate limits on the endpoints that cost the server something."""
import time

from tests.conftest import new_client


def test_login_attempts_are_limited_per_ip(client):
    new_client("carol", "hunter22")
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "carol", "password": "no"}).status_code == 403
    r = client.post("/api/auth/login", json={"username": "carol", "password": "hunter22"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert "try again" in r.get_json()["error"]


def test_limits_are_per_client_ip(client):
    new_client("carol", "hunter22")
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "carol", "password": "no"})
    other = client.post("/api/auth/login", json={"username": "carol", "password": "hunter22"},
                        headers={"Fly-Client-IP": "203.0.113.9"})
    assert other.status_code == 200


def test_pin_claims_are_limited(client, db):
    nav = new_client("nav")
    for _ in range(5):
        assert nav.post("/api/boats/1/claim", json={"pin": "0000"}).status_code == 404
    assert nav.post("/api/boats/1/claim", json={"pin": "0000"}).status_code == 429


def test_route_submissions_are_limited_per_boat(client):
    import app as appmod
    admin = new_client("admin")
    from tests.test_api import race_body
    race = admin.post("/api/races", json=race_body()).get_json()["id"]
    boat = admin.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    wps = {"waypoints": [[-0.5, 0.0]]}
    for _ in range(appmod.RATE_LIMITS["route"][0]):
        assert admin.post(f"/api/boats/{boat}/route", json=wps).status_code == 200
    assert admin.post(f"/api/boats/{boat}/route", json=wps).status_code == 429


def test_window_expires(client, monkeypatch):
    import app as appmod
    new_client("carol", "hunter22")
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "carol", "password": "no"})
    assert client.post("/api/auth/login", json={"username": "carol", "password": "hunter22"}).status_code == 429
    real = time.time
    monkeypatch.setattr(appmod.time, "time", lambda: real() + 61)
    assert client.post("/api/auth/login", json={"username": "carol", "password": "hunter22"}).status_code == 200


def test_route_log_keeps_the_last_fifty(client, db):
    import app as appmod
    admin = new_client("admin")
    from tests.test_api import race_body
    race = admin.post("/api/races", json=race_body()).get_json()["id"]
    boat = admin.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    monkeypatch_limit = appmod.RATE_LIMITS["route"]
    appmod.RATE_LIMITS["route"] = (1000, 3600)
    try:
        for i in range(60):
            assert admin.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]}).status_code == 200
    finally:
        appmod.RATE_LIMITS["route"] = monkeypatch_limit
    n = db.execute("SELECT COUNT(*) c FROM route_log WHERE boat_id=?", (boat,)).fetchone()["c"]
    assert n == 50
