"""The committed race definitions are titled by their course, not by a
sponsor's or organiser's mark, and name the official race once, factually,
in the first line of the description.  A race page is the first thing an
organiser will look at; this keeps their trademarks out of our titles."""
import glob
import json
import os
import re

RACE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "races")
MARKS = ("rolex", "race", "regatta", "rorc", "virtual", "fantasy")


def _races():
    files = sorted(glob.glob(os.path.join(RACE_DIR, "*.json")))
    assert files, "no race definitions found"
    for f in files:
        with open(f, encoding="utf-8") as fh:
            yield os.path.basename(f), json.load(fh)


def test_titles_are_courses_not_brands():
    for fname, d in _races():
        low = d["name"].lower()
        for w in MARKS:
            assert not re.search(rf"\b{w}\b", low), f"{fname}: {d['name']!r} carries {w!r}"


def test_description_names_the_official_race_once():
    for fname, d in _races():
        desc = d.get("description", "")
        if d.get("rolling"):
            # the practice course has no real fleet and no organiser
            assert "alongside" not in desc, fname
            continue
        assert desc.startswith("Sailed alongside the "), f"{fname}: {desc[:60]!r}"
        assert "rolex" not in desc.lower(), fname
        assert "virtual edition" not in desc.lower(), fname
