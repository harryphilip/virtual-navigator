"""Race documents are the organiser's: uploads are visible to admins only,
and competitors get a link to the organiser's own documents page."""
import time

from tests.conftest import new_client
from tests.test_api import make_race_via_api, race_body


def _attach(db, race, filename="SIs.pdf"):
    doc = db.execute(
        "INSERT INTO race_docs(race_id, kind, filename, mime, content, uploaded_at) "
        "VALUES (?,?,?,?,?,?)",
        (race, "si", filename, "application/pdf", b"%PDF-1.4 fake", int(time.time()))).lastrowid
    db.commit()
    return doc


def test_uploaded_documents_are_not_republished(client, db):
    admin, race = make_race_via_api()
    doc = _attach(db, race)
    nav = new_client("nav")
    # signed out and signed in alike: nothing listed, nothing served
    assert client.get(f"/api/races/{race}/docs").get_json() == []
    assert nav.get(f"/api/races/{race}/docs").get_json() == []
    assert client.get(f"/api/docs/{doc}").status_code == 403
    assert nav.get(f"/api/docs/{doc}").status_code == 403
    # the admin who imported them still sees them
    listed = admin.get(f"/api/races/{race}/docs").get_json()
    assert [d["filename"] for d in listed] == ["SIs.pdf"]
    got = admin.get(f"/api/docs/{doc}")
    assert got.status_code == 200 and got.data.startswith(b"%PDF")


def test_docs_url_is_set_at_creation_and_public(client):
    admin = new_client("admin")
    r = admin.post("/api/races", json=dict(race_body(), docs_url=" https://example.org/race/docs "))
    race = r.get_json()["id"]
    info = client.get(f"/api/races/{race}").get_json()
    assert info["docs_url"] == "https://example.org/race/docs"


def test_docs_url_defaults_to_empty(client):
    admin, race = make_race_via_api()
    assert client.get(f"/api/races/{race}").get_json()["docs_url"] == ""
