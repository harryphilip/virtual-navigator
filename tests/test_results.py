"""Official results: parsing, roster matching, applying, and what the
leaderboard then shows."""
from tests.conftest import make_race, new_client
from vn import results
from vn.realfleet import ingest_points

MARKS = [("Start", 0.0, 0.0), ("Finish", -0.5, 0.0)]


def ys_payload():
    """The shape api.yachtscoring.com returns for result-detail-report."""
    def boat(name, status, finish, elapsed, corrected, place, sail=("USA", "42")):
        return {"name": name, "sailPrefix": sail[0], "sailNumber": sail[1], "finishStatus": status,
                "finishTime": finish, "elapsedTime": elapsed, "correctedTime": corrected,
                "placeClass": place, "placeOverall": place}
    return {"eventData": {}, "data": [{"circleName": "Vineyard", "divisions": [{
        "divisionName": "ORC", "classes": [
            {"className": "Class 40", "boats": [
                boat("Moneyball", "AOK", "2026-09-05T13:02:11.000Z", 75731, 70112, 1, ("USA", "153")),
                boat("Midnight Rider - PMP Strategy", "AOK", "2026-09-05T14:10:00.000Z", 79800, 71000, 2, (None, "77")),
                boat("Jamala", "DNS", None, None, None, 4, ("USA", "1358")),
                boat("Nobody Here", "RET", None, None, None, 3, ("USA", "9")),
            ]}]}]}]}


def fleet(db):
    race_id = make_race(db, MARKS, start_time=1_000_000)
    for name, sail in (("Moneyball", "USA 153"), ("Midnight Rider", None), ("Jamala", None),
                       ("Celebration (II)", "USA 5")):
        db.execute("INSERT INTO real_boats(race_id,name,klass,sail_no) VALUES (?,?,?,?)",
                   (race_id, name, "Class 40", sail))
    db.commit()
    return race_id


def test_parse_source_forms():
    assert results.parse_source("yachtscoring:50775") == ("yachtscoring", "50775", 1)
    assert results.parse_source("yachtscoring:50775#2") == ("yachtscoring", "50775", 2)
    assert results.parse_source("https://www.yachtscoring.com/emenu/50775") == ("yachtscoring", "50775", 1)
    assert results.parse_source("https://yachtscoring.com/event_results_cumulative.cfm?eID=15833")[1] == "15833"
    try:
        results.parse_source("regattaresults.com/xyz")
        assert False
    except ValueError as e:
        assert "Yacht Scoring" in str(e)


def test_parse_yachtscoring_rows():
    rows = results.parse_yachtscoring(ys_payload())
    assert [r["name"] for r in rows] == ["Moneyball", "Midnight Rider - PMP Strategy", "Jamala", "Nobody Here"]
    m = rows[0]
    assert m["status"] == "FIN" and m["finish_at"] == 1788613331 and m["elapsed_s"] == 75731
    assert m["sail_no"] == "USA 153" and m["klass"] == "Class 40" and m["place_class"] == 1
    assert rows[1]["sail_no"] == "77"
    assert rows[2]["status"] == "DNS" and rows[2]["finish_at"] is None
    assert rows[3]["status"] == "RET"


def test_parse_csv_rows():
    text = ("Pos,Yacht,Sail #,Class,Finish Time (UTC),Elapsed,Corrected,Status\n"
            "1,Moneyball,USA 153,Class 40,2026-09-05 13:02:11,21:02:11,19:28:32,\n"
            "2,Celebration,USA 5,Class 40,2026-09-05T15:00:00Z,1d 00:00:00,,FIN\n"
            ",Jamala,,Class 40,,,,RET\n")
    rows = results.parse_csv(text)
    assert rows[0]["finish_at"] == 1788613331 and rows[0]["elapsed_s"] == 75731
    assert rows[0]["corrected_s"] == 19 * 3600 + 28 * 60 + 32 and rows[0]["place_class"] == 1
    assert rows[1]["elapsed_s"] == 86400 and rows[1]["status"] == "FIN"
    assert rows[2]["status"] == "RET" and rows[2]["finish_at"] is None


def test_matching_by_sail_then_name_with_sponsor_suffixes(db):
    race_id = fleet(db)
    rows = results.parse_yachtscoring(ys_payload())
    matches, unmatched, roster_left = results.match_roster(db, race_id, rows)
    got = {rb["name"]: res["name"] for rb, res in matches}
    assert got == {"Moneyball": "Moneyball", "Midnight Rider": "Midnight Rider - PMP Strategy",
                   "Jamala": "Jamala"}
    assert [r["name"] for r in unmatched] == ["Nobody Here"]
    assert [rb["name"] for rb in roster_left] == ["Celebration (II)"]


def test_apply_writes_official_results_and_the_log(db):
    race_id = fleet(db)
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    matches, _, _ = results.match_roster(db, race_id, results.parse_yachtscoring(ys_payload()))
    summary = results.apply_results(db, race, matches, "yachtscoring:50775#1", now=2_000_000)
    assert summary == {"finishers": 2, "non_finishers": 1, "matched": 3}
    mb = db.execute("SELECT * FROM real_boats WHERE name='Moneyball'").fetchone()
    assert mb["official_status"] == "FIN" and mb["finished_at"] == 1788613331
    assert mb["official_elapsed_s"] == 75731 and mb["official_corrected_s"] == 70112
    assert mb["official_place"] == 1 and mb["official_class"] == "Class 40"
    mr = db.execute("SELECT * FROM real_boats WHERE name='Midnight Rider'").fetchone()
    assert mr["sail_no"] == "77"                       # filled in from the results
    ja = db.execute("SELECT * FROM real_boats WHERE name='Jamala'").fetchone()
    assert ja["official_status"] == "DNS" and ja["finished_at"] is None
    r = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    assert r["results_source"] == "yachtscoring:50775#1" and r["results_at"] == 2_000_000
    log = db.execute("SELECT message FROM race_log WHERE race_id=? ORDER BY id DESC",
                     (race_id,)).fetchone()["message"]
    assert "Official results imported" in log and "2 finishers" in log


def test_tracker_updates_never_overwrite_an_official_finish(db):
    race_id = fleet(db)
    race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
    marks = db.execute("SELECT * FROM marks WHERE race_id=? ORDER BY seq", (race_id,)).fetchall()
    mb = db.execute("SELECT id FROM real_boats WHERE name='Moneyball'").fetchone()["id"]
    ja = db.execute("SELECT id FROM real_boats WHERE name='Jamala'").fetchone()["id"]
    matches, _, _ = results.match_roster(db, race_id, results.parse_yachtscoring(ys_payload()))
    results.apply_results(db, race, matches, "test")
    # a late tracker fix at the finish mark arrives after the results
    ingest_points(db, race, marks, mb, [(1788613331 + 600, -0.5, 0.0)], now=1_900_000_000)
    assert db.execute("SELECT finished_at FROM real_boats WHERE id=?", (mb,)).fetchone()[0] == 1788613331
    # a boat scored DNS that drifts past the finish mark stays DNS
    ingest_points(db, race, marks, ja, [(1788613331 + 600, -0.5, 0.0)], now=1_900_000_000)
    assert db.execute("SELECT finished_at FROM real_boats WHERE id=?", (ja,)).fetchone()[0] is None
    # a backfill rebuilds tracker state but keeps the committee's finish
    ingest_points(db, race, marks, mb, [(1788613331 - 3600, -0.4, 0.0)], now=1_900_000_000)
    assert db.execute("SELECT finished_at FROM real_boats WHERE id=?", (mb,)).fetchone()[0] == 1788613331


def test_api_preview_then_apply_is_admin_only(client, db, monkeypatch):
    race_id = fleet(db)
    monkeypatch.setattr(results, "_http_json", lambda url, **kw: ys_payload())
    admin = new_client("admin")                     # first account: the admin
    nav = new_client("nav")
    body = {"source": "yachtscoring:50775"}
    assert nav.post(f"/api/races/{race_id}/results", json=body).status_code == 403
    r = admin.post(f"/api/races/{race_id}/results", json=body)
    assert r.status_code == 200
    j = r.get_json()
    assert j["applied"] is False and len(j["matched"]) == 3
    assert j["unmatched_results"][0]["name"] == "Nobody Here"
    assert j["unmatched_roster"][0]["boat"] == "Celebration (II)"
    assert db.execute("SELECT official_status FROM real_boats WHERE name='Moneyball'").fetchone()[0] is None
    r = admin.post(f"/api/races/{race_id}/results", json=dict(body, apply=True))
    assert r.status_code == 200 and r.get_json()["applied"] is True
    assert r.get_json()["summary"]["finishers"] == 2
    state = client.get(f"/api/races/{race_id}/state").get_json()
    by = {e["name"]: e for e in state["entries"]}
    assert by["Moneyball"]["finished_at"] == 1788613331
    assert by["Moneyball"]["official"]["elapsed_s"] == 75731 and by["Moneyball"]["official"]["place_class"] == 1
    assert by["Jamala"]["official"]["status"] == "DNS"
    assert by["Celebration (II)"]["official"] is None
    detail = client.get(f"/api/races/{race_id}").get_json()
    assert detail["results"]["source"] == "yachtscoring:50775#1"
    pub = client.get(f"/api/races/{race_id}/results").get_json()
    assert [r["boat"] for r in pub["boats"]][:1] == ["Moneyball"]
    assert pub["boats"][0]["corrected_s"] == 70112


def test_api_accepts_pasted_csv_and_rejects_junk(client, db):
    race_id = fleet(db)
    admin = new_client("admin")
    r = admin.post(f"/api/races/{race_id}/results", json={"source": "somewhere else"})
    assert r.status_code == 400 and "Yacht Scoring" in r.get_json()["error"]
    csv_text = "Boat,Finish,Elapsed,Status\nMoneyball,2026-09-05T13:02:11Z,21:02:11,\nJamala,,,RET\n"
    r = admin.post(f"/api/races/{race_id}/results", json={"csv": csv_text, "apply": True})
    assert r.status_code == 200 and r.get_json()["summary"]["matched"] == 2
    assert db.execute("SELECT official_status FROM real_boats WHERE name='Jamala'").fetchone()[0] == "RET"


def test_import_script_previews_and_applies(db, monkeypatch, capsys):
    import importlib
    race_id = fleet(db)
    monkeypatch.setattr(results, "_http_json", lambda url, **kw: ys_payload())
    script = importlib.import_module("scripts.import_results")
    script.main([str(race_id), "yachtscoring:50775"])
    out = capsys.readouterr().out
    assert "3 matched" in out and "preview only" in out and "Nobody Here" in out
    assert db.execute("SELECT official_status FROM real_boats WHERE name='Moneyball'").fetchone()[0] is None
    script.main([str(race_id), "yachtscoring:50775", "--apply"])
    assert db.execute("SELECT official_status FROM real_boats WHERE name='Moneyball'").fetchone()[0] == "FIN"
