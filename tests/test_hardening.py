"""Headers, error pages, crawler files."""


def test_security_headers_on_every_response(client):
    h = client.get("/").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "strict-origin" in h["Referrer-Policy"]
    assert "geolocation=()" in h["Permissions-Policy"]
    assert "Strict-Transport-Security" not in h          # plain http in tests
    secure = client.get("/", base_url="https://localhost").headers
    assert secure["Strict-Transport-Security"].startswith("max-age=")


def test_vendored_assets_are_cacheable(client):
    h = client.get("/vendor/leaflet/leaflet.css").headers
    assert "immutable" in h["Cache-Control"]
    assert "immutable" not in client.get("/style.css").headers.get("Cache-Control", "")


def test_404_is_branded_for_pages_and_json_for_the_api(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert "Virtual Navigator" in r.get_data(as_text=True)
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.is_json


def test_robots_and_favicon(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /api/" in r.get_data(as_text=True)
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"


def test_unhandled_errors_are_logged_and_answered_plainly(client, caplog):
    import app as appmod
    with appmod.app.test_request_context("/api/boom"):
        body, code = appmod.handle_error(ZeroDivisionError("boom"))
    assert code == 500 and "logged" in body.get_json()["error"]
    assert any("500 on GET /api/boom" in m for m in caplog.messages)
