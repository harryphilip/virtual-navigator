"""HTTP surface: accounts, the permission matrix, race creation, routing."""
import time

from tests.conftest import POLAR_40FT, new_client

MARKS = [{"name": "Start", "lat": 0.0, "lon": 0.0},
         {"name": "Finish", "lat": "0° 30.0' S", "lon": 0.0}]


def race_body(**over):
    body = {"name": "API Race", "start_time": int(time.time()) - 3600,
            "polar_text": POLAR_40FT, "marks": MARKS, "currents_enabled": False}
    body.update(over)
    return body


# ---- accounts ---------------------------------------------------------------

def test_first_account_is_admin_second_is_not(client):
    a = new_client("alice")
    assert a.get("/api/auth/me").get_json()["user"]["is_admin"] is True
    b = new_client("bob")
    assert b.get("/api/auth/me").get_json()["user"]["is_admin"] is False


def test_register_validation(client):
    assert client.post("/api/auth/register", json={"username": "x", "password": "secret1"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "okname", "password": "123"}).status_code == 400
    new_client("taken")
    r = client.post("/api/auth/register", json={"username": "Taken", "password": "secret1"})
    assert r.status_code == 409


def test_login_logout(client):
    new_client("carol", "hunter22")
    assert client.post("/api/auth/login", json={"username": "carol", "password": "wrong"}).status_code == 403
    assert client.post("/api/auth/login", json={"username": "CAROL", "password": "hunter22"}).status_code == 200
    assert client.get("/api/auth/me").get_json()["user"]["username"] == "carol"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").get_json()["user"] is None


def test_profile_404(client):
    assert client.get("/api/users/nobody").status_code == 404


# ---- races ------------------------------------------------------------------

def test_only_admins_create_races(client):
    assert client.post("/api/races", json=race_body()).status_code == 403
    admin = new_client("admin")
    user = new_client("user")
    assert user.post("/api/races", json=race_body()).status_code == 403
    r = admin.post("/api/races", json=race_body())
    assert r.status_code == 200
    race = client.get(f"/api/races/{r.get_json()['id']}").get_json()
    assert [m["name"] for m in race["marks"]] == ["Start", "Finish"]
    assert race["marks"][1]["lat"] == -0.5


def test_race_creation_rejects_bad_input(client):
    admin = new_client("admin")
    assert admin.post("/api/races", json=race_body(marks=MARKS[:1])).status_code == 400
    assert admin.post("/api/races", json=race_body(polar_text="junk")).status_code == 400
    bad = dict(MARKS[0], lat="somewhere")
    assert admin.post("/api/races", json=race_body(marks=[bad, MARKS[1]])).status_code == 400
    sided = dict(MARKS[1], side="left")
    assert admin.post("/api/races", json=race_body(marks=[MARKS[0], sided])).status_code == 400


def test_race_listing_and_404(client):
    assert client.get("/api/races").get_json() == []
    assert client.get("/api/races/99").status_code == 404
    assert client.get("/api/races/99/state").status_code == 404


# ---- boats and routing -----------------------------------------------------

def make_race_via_api(**over):
    admin = new_client("admin")
    r = admin.post("/api/races", json=race_body(**over))
    assert r.status_code == 200, r.get_json()
    return admin, r.get_json()["id"]


def test_boat_registration_matrix(client):
    admin, race = make_race_via_api()
    assert client.post(f"/api/races/{race}/boats", json={"name": "Anon"}).status_code == 401
    nav = new_client("nav")
    assert nav.post(f"/api/races/{race}/boats", json={"name": ""}).status_code == 400
    r = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"})
    assert r.status_code == 200
    assert nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).status_code == 409
    assert nav.post("/api/races/99/boats", json={"name": "Magpie"}).status_code == 404
    mine = nav.get(f"/api/races/{race}/my_boats").get_json()
    assert [b["name"] for b in mine] == ["Magpie"]
    assert client.get(f"/api/races/{race}/my_boats").get_json() == []


def test_route_submission_matrix(client):
    admin, race = make_race_via_api()
    nav = new_client("nav")
    other = new_client("other")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    wps = {"waypoints": [[-0.2, 0.0], [-0.5, 0.0]]}
    assert client.post(f"/api/boats/{boat}/route", json=wps).status_code == 401
    assert other.post(f"/api/boats/{boat}/route", json=wps).status_code == 403
    assert nav.post(f"/api/boats/{boat}/route", json={"waypoints": []}).status_code == 400
    assert nav.post("/api/boats/99/route", json=wps).status_code == 404
    r = nav.post(f"/api/boats/{boat}/route", json=wps)
    assert r.status_code == 200
    assert r.get_json()["waypoints"] == 2
    # an admin may steer any boat
    assert admin.post(f"/api/boats/{boat}/route", json=wps).status_code == 200


def test_first_submission_starts_the_boat_on_the_line(client):
    admin, race = make_race_via_api(start_time=int(time.time()) - 7200)
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    state = client.get(f"/api/races/{race}/state").get_json()
    me = [e for e in state["entries"] if e["name"] == "Magpie"][0]
    assert me["started"] is True and me["has_route"] is True
    assert me["owner"] == "nav"
    # late entry: starts now, not backdated to the gun two hours ago
    assert abs(me["dtf"] - state["course_len_nm"]) < 0.5
    assert me["rank"] == 1


def test_gpx_upload_is_reconciled_with_the_course(client):
    admin, race = make_race_via_api()
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    gpx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1"><rte>'
           '<rtept lat="-0.2" lon="0.0"/><rtept lat="-0.4" lon="0.0"/></rte></gpx>')
    r = nav.post(f"/api/boats/{boat}/route", json={"gpx": gpx})
    assert r.status_code == 200
    body = r.get_json()
    assert body["waypoints"] == 3                     # finish appended
    assert any("Finish" in n for n in body["adjustments"])
    assert nav.post(f"/api/boats/{boat}/route", json={"gpx": "<gpx><broken"}).status_code == 400


def test_uploads_pause_only_for_the_race_whose_weather_is_degraded(client, db):
    admin, race = make_race_via_api()
    far = [{"name": "Start", "lat": 40.0, "lon": -70.0},
           {"name": "Finish", "lat": 40.5, "lon": -70.0}]
    other = admin.post("/api/races", json=race_body(name="Far Race", marks=far)).get_json()["id"]
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    boat_far = nav.post(f"/api/races/{other}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    now = int(time.time())
    db.execute("INSERT INTO wind_cache(lat,lon,t,twd,tws,source) VALUES (0,0,?,0,10,'synthetic')",
               ((now // 3600) * 3600,))
    db.commit()
    r = nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    assert r.status_code == 503
    assert client.get(f"/api/races/{race}/state").get_json()["weather"]["degraded"] is True
    # the race 40° away is unaffected
    r = nav.post(f"/api/boats/{boat_far}/route", json={"waypoints": [[40.5, -70.0]]})
    assert r.status_code == 200
    assert client.get(f"/api/races/{other}/state").get_json()["weather"]["degraded"] is False
