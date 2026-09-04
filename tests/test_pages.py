"""The static pages are served and reference each other."""
import pytest


@pytest.mark.parametrize("path,needle", [
    ("/", "Route the real race"),
    ("/race", "Enter this race"),
    ("/user", "Route history"),
    ("/how", "How the race is sailed"),
])
def test_pages_are_served(client, path, needle):
    r = client.get(path)
    assert r.status_code == 200
    assert needle in r.get_data(as_text=True)


def test_every_page_links_to_how_it_works_and_carries_the_footer(client):
    for path in ("/", "/race", "/user", "/how"):
        html = client.get(path).get_data(as_text=True)
        assert 'href="/how"' in html, path
        assert "Not for navigation" in html, path
