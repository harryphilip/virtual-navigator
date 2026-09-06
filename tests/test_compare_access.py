"""Who may see the real-vs-virtual analysis, and how much of it.

A real boat's track is public; a verdict on it ("sailed at 71 % of polar")
is a judgment on a named crew, so it is not. Anyone who has entered a boat
in the race can ask for the gap split against a real boat; only an admin
gets the real boat's percentage and per-band table."""
from tests.conftest import new_client
from tests.test_api import make_race_via_api


def _real_boat(db, race, name="Pincoya"):
    rb = db.execute("INSERT INTO real_boats(race_id, name, klass) VALUES (?,?,?)",
                    (race, name, "J/122")).lastrowid
    db.commit()
    return rb


def test_signed_out_gets_401(client, db):
    admin, race = make_race_via_api()
    rb = _real_boat(db, race)
    r = client.get(f"/api/races/{race}/compare?real={rb}")
    assert r.status_code == 401


def test_navigator_without_a_boat_in_the_race_gets_403(client, db):
    admin, race = make_race_via_api()
    rb = _real_boat(db, race)
    nav = new_client("nav")
    assert nav.get(f"/api/races/{race}/compare?real={rb}").status_code == 403


def test_navigator_with_a_boat_must_name_a_virtual_boat(client, db):
    admin, race = make_race_via_api()
    rb = _real_boat(db, race)
    nav = new_client("nav")
    nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"})
    r = nav.get(f"/api/races/{race}/compare?real={rb}")
    assert r.status_code == 400
    assert "virtual" in r.get_json()["error"].lower()


def test_admin_alone_sees_the_polar_verdict(client, db, monkeypatch):
    import app as app_module
    admin, race = make_race_via_api()
    rb = _real_boat(db, race)
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]

    full = {"real": {"name": "Pincoya", "pct_polar": 71.0, "by_pos": [], "by_tws": [],
                     "distance_nm": 10.0, "elapsed_h": 2.0},
            "virtual": {"name": "Magpie", "pct_polar": 100.0},
            "components": {"boat_speed_h": 0.5, "navigation_h": -0.2, "start_h": 0.0},
            "gap_h": 0.3, "notes": [], "reference": "x", "perf_factor": 0.9,
            "polar_name": "p", "target_dtf_nm": 1.0}
    monkeypatch.setattr(app_module, "compare", lambda *a, **k: full)
    app_module._compare_memo.clear()

    seen = nav.get(f"/api/races/{race}/compare?real={rb}&virtual={boat}")
    assert seen.status_code == 200
    real = seen.get_json()["real"]
    assert "pct_polar" not in real and "by_pos" not in real and "by_tws" not in real
    assert seen.get_json()["components"]["boat_speed_h"] == 0.5     # the split stays
    assert seen.get_json()["virtual"]["pct_polar"] == 100.0         # their own boat

    # the memo serves both callers; the admin still gets everything
    whole = admin.get(f"/api/races/{race}/compare?real={rb}&virtual={boat}").get_json()
    assert whole["real"]["pct_polar"] == 71.0 and whole["real"]["by_pos"] == []
    # and a plain polar report (no virtual boat) is the admin's alone
    assert admin.get(f"/api/races/{race}/compare?real={rb}").status_code == 200


def test_unstarted_entries_carry_no_rank(client, db):
    admin, race = make_race_via_api()
    _real_boat(db, race, "Never Reported")
    # gate off: a real boat that never reports would otherwise hold the
    # virtual fleet on the line, which is a different test
    db.execute("UPDATE races SET fleet_start_pct=0 WHERE id=?", (race,))
    db.commit()
    nav = new_client("nav")
    boat = nav.post(f"/api/races/{race}/boats", json={"name": "Magpie"}).get_json()["boat_id"]
    nav.post(f"/api/boats/{boat}/route", json={"waypoints": [[-0.5, 0.0]]})
    entries = client.get(f"/api/races/{race}/state").get_json()["entries"]
    by = {e["name"]: e for e in entries}
    assert by["Magpie"]["rank"] == 1
    assert by["Never Reported"]["started"] is False
    assert by["Never Reported"]["rank"] is None
