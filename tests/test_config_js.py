"""/js/config.js hands the pages the tile source: OpenStreetMap unless a
provider is configured through the environment."""
import json
import re


def _cfg(client):
    r = client.get("/js/config.js")
    assert r.status_code == 200 and r.mimetype == "application/javascript"
    m = re.fullmatch(r"window\.VN_CONFIG = (.*);\n", r.data.decode())
    return json.loads(m.group(1))


def test_defaults_to_osm(client, monkeypatch):
    monkeypatch.delenv("VN_TILE_URL", raising=False)
    monkeypatch.delenv("VN_TILE_ATTRIBUTION", raising=False)
    cfg = _cfg(client)
    assert cfg == {"tile_url": "", "tile_attribution": "", "tile_max_zoom": None}


def test_provider_from_the_environment(client, monkeypatch):
    monkeypatch.setenv("VN_TILE_URL", "https://tiles.example.com/{z}/{x}/{y}.png?key=abc ")
    monkeypatch.setenv("VN_TILE_ATTRIBUTION", "&copy; Example Tiles, &copy; OpenStreetMap contributors")
    monkeypatch.setenv("VN_TILE_MAX_ZOOM", "20")
    cfg = _cfg(client)
    assert cfg["tile_url"] == "https://tiles.example.com/{z}/{x}/{y}.png?key=abc"
    assert cfg["tile_attribution"].startswith("&copy; Example")
    assert cfg["tile_max_zoom"] == 20


def test_pages_load_config_before_the_shared_module(client):
    for path in ("/", "/race"):
        html = client.get(path).data.decode()
        assert html.index("/js/config.js") < html.index("/js/vn.js"), path
        assert "tile.openstreetmap.org" not in html, path
