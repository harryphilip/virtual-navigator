"""scripts/add_mark.py inserts a course mark and renumbers the rest; it
refuses once a boat has started."""
import importlib.util
import os
import sys
import time

import pytest

from tests.conftest import make_boat, make_race

START = ("Start", 0.0, 0.0)
FINISH = ("Finish", -1.0, 0.0)


def _script():
    spec = importlib.util.spec_from_file_location(
        "add_mark", os.path.join(os.path.dirname(__file__), "..", "scripts", "add_mark.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_insert_renumbers_and_logs(db, monkeypatch, capsys):
    race = make_race(db, [START, FINISH])
    monkeypatch.setattr(sys, "argv", ["add_mark.py", str(race), "1", "-0.5", "0.2", "Clearance", "stbd"])
    _script().main()
    marks = db.execute("SELECT seq, name, lat, lon, side FROM marks WHERE race_id=? ORDER BY seq",
                       (race,)).fetchall()
    assert [(m["seq"], m["name"]) for m in marks] == [(0, "Start"), (1, "Clearance"), (2, "Finish")]
    assert marks[1]["side"] == "stbd" and marks[1]["lon"] == 0.2
    log = db.execute("SELECT message FROM race_log WHERE race_id=?", (race,)).fetchone()["message"]
    assert log.startswith("Mark added at position 1: Clearance")
    assert "3 marks now" in capsys.readouterr().out


def test_refused_once_a_boat_has_started(db, monkeypatch, capsys):
    race = make_race(db, [START, FINISH])
    make_boat(db, race, name="Out", started_at=int(time.time()) - 3600)
    monkeypatch.setattr(sys, "argv", ["add_mark.py", str(race), "1", "-0.5", "0.2", "Clearance"])
    with pytest.raises(SystemExit):
        _script().main()
    assert db.execute("SELECT COUNT(*) c FROM marks WHERE race_id=?", (race,)).fetchone()["c"] == 2
    assert "have started" in capsys.readouterr().out
