"""One public address: www.<canonical host> is redirected to the bare domain,
and every other hostname is served as-is."""
import app as appmod


def test_www_redirects_to_canonical(monkeypatch):
    monkeypatch.setattr(appmod, "CANONICAL_HOST", "virtual-navigator.com")
    c = appmod.app.test_client()
    r = c.get("/race.html?race=3", base_url="https://www.virtual-navigator.com")
    assert r.status_code == 301
    assert r.headers["Location"] == "https://virtual-navigator.com/race.html?race=3"
    r = c.get("/how", base_url="https://www.virtual-navigator.com")
    assert r.headers["Location"] == "https://virtual-navigator.com/how"


def test_other_hosts_are_served(monkeypatch):
    monkeypatch.setattr(appmod, "CANONICAL_HOST", "virtual-navigator.com")
    c = appmod.app.test_client()
    for host in ("https://virtual-navigator.com", "https://virtual-navigator.fly.dev",
                 "http://localhost:5170"):
        assert c.get("/healthz", base_url=host).status_code == 200


def test_unset_means_no_redirect(monkeypatch):
    monkeypatch.setattr(appmod, "CANONICAL_HOST", "")
    c = appmod.app.test_client()
    assert c.get("/healthz", base_url="https://www.virtual-navigator.com").status_code == 200
