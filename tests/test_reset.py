"""Password reset by email: opt-in address, one-hour single-use links, no
account enumeration, every other session signed out on change."""
import re
import time

import pytest

import vn.mail as mail
from tests.conftest import new_client


@pytest.fixture(autouse=True)
def console_mail(monkeypatch):
    monkeypatch.setenv("MAIL_BACKEND", "console")
    mail.SENT.clear()


def register(username, email=None, password="secret1"):
    import app as appmod
    c = appmod.app.test_client()
    body = {"username": username, "password": password, "display_name": username}
    if email is not None:
        body["email"] = email
    r = c.post("/api/auth/register", json=body)
    return c, r


def link_from(msg):
    m = re.search(r"https?://\S+/reset\?token=([A-Za-z0-9_\-]+)", msg["text"])
    assert m, msg["text"]
    return m.group(1)


def test_email_is_optional_and_validated(client):
    c, r = register("alice", "Alice@Example.com")
    assert r.status_code == 200
    assert c.get("/api/auth/me").get_json()["user"]["email"] == "alice@example.com"
    assert register("bob", "not-an-email")[1].status_code == 400
    assert register("carol", "alice@example.com")[1].status_code == 409   # taken
    c2, r2 = register("dave")
    assert r2.status_code == 200
    assert c2.get("/api/auth/me").get_json()["user"]["email"] is None


def test_set_and_clear_email_when_signed_in(client):
    c, _ = register("alice")
    assert client.post("/api/auth/email", json={"email": "x@y.zz"}).status_code == 401
    assert c.post("/api/auth/email", json={"email": "bad"}).status_code == 400
    assert c.post("/api/auth/email", json={"email": "Alice@Example.com"}).status_code == 200
    assert c.get("/api/auth/me").get_json()["user"]["email"] == "alice@example.com"
    assert c.post("/api/auth/email", json={"email": ""}).status_code == 200
    assert c.get("/api/auth/me").get_json()["user"]["email"] is None


def test_forgot_sends_one_link_by_username_or_email_and_never_enumerates(client):
    register("alice", "alice@example.com")
    register("bob")                                       # no email on file
    for i, who in enumerate(("alice", "ALICE@example.com", "bob", "nobody")):
        r = client.post("/api/auth/forgot", json={"account": who},
                        headers={"Fly-Client-IP": f"203.0.113.{i}"})    # one client each
        assert r.status_code == 200, who
        assert "on file" in r.get_json()["message"]
    assert len(mail.SENT) == 2
    assert {m["to"] for m in mail.SENT} == {"alice@example.com"}
    assert "alice" in mail.SENT[0]["text"] and "/reset?token=" in mail.SENT[0]["text"]


def test_reset_changes_the_password_signs_out_other_sessions_and_burns_the_token(client, db):
    c, _ = register("alice", "alice@example.com", password="oldpass1")
    client.post("/api/auth/forgot", json={"account": "alice"})
    token = link_from(mail.SENT[-1])
    assert client.post("/api/auth/reset", json={"token": token, "password": "123"}).status_code == 400
    r = client.post("/api/auth/reset", json={"token": token, "password": "newpass1"})
    assert r.status_code == 200
    assert r.get_json()["user"]["username"] == "alice"
    # the resetting client is signed in; the old session is not
    assert client.get("/api/auth/me").get_json()["user"]["username"] == "alice"
    assert c.get("/api/auth/me").get_json()["user"] is None
    assert client.post("/api/auth/login", json={"username": "alice", "password": "oldpass1"}).status_code == 403
    assert client.post("/api/auth/login", json={"username": "alice", "password": "newpass1"}).status_code == 200
    # once only
    assert client.post("/api/auth/reset", json={"token": token, "password": "another1"}).status_code == 400


def test_reset_links_expire_after_an_hour(client, db):
    register("alice", "alice@example.com")
    client.post("/api/auth/forgot", json={"account": "alice"})
    token = link_from(mail.SENT[-1])
    db.execute("UPDATE password_resets SET expires_at=?", (int(time.time()) - 1,))
    db.commit()
    r = client.post("/api/auth/reset", json={"token": token, "password": "newpass1"})
    assert r.status_code == 400
    assert "expired" in r.get_json()["error"].lower() or "not valid" in r.get_json()["error"].lower()
    assert client.post("/api/auth/reset", json={"token": "garbage", "password": "newpass1"}).status_code == 400


def test_forgot_says_so_when_mail_is_off(client, monkeypatch):
    register("alice", "alice@example.com")
    monkeypatch.setenv("MAIL_BACKEND", "off")
    r = client.post("/api/auth/forgot", json={"account": "alice"})
    assert r.status_code == 503
    assert client.get("/api/auth/me").get_json()["reset_available"] is False
    monkeypatch.setenv("MAIL_BACKEND", "console")
    assert client.get("/api/auth/me").get_json()["reset_available"] is True


def test_forgot_is_rate_limited(client):
    register("alice", "alice@example.com")
    for _ in range(3):
        assert client.post("/api/auth/forgot", json={"account": "alice"}).status_code == 200
    assert client.post("/api/auth/forgot", json={"account": "alice"}).status_code == 429
    assert len(mail.SENT) == 3


def test_reset_page_is_served(client):
    r = client.get("/reset?token=abc")
    assert r.status_code == 200 and "Choose a new password" in r.get_data(as_text=True)
