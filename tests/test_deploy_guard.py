"""The production deploy guard: freeze around a gun, one deploy a day while
a real race runs, the practice course never in the way, force overrides."""
import importlib.util
import os
import time

spec = importlib.util.spec_from_file_location(
    "deploy_guard", os.path.join(os.path.dirname(__file__), "..", "scripts", "deploy_guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

NOW = 1_800_000_000
H = 3600


def race(name, start, status, rolling=False):
    return {"name": name, "start_time": start, "status": status, "rolling": rolling}


def test_quiet_board_deploys():
    go, why = guard.decide(NOW, [race("Malta round Sicily 606", NOW + 30 * 24 * H, "upcoming")], NOW - 2 * H)
    assert go and "no real race" in why


def test_freeze_before_and_after_a_gun():
    r = [race("Sydney to Hobart", NOW + 90 * 60, "upcoming")]
    go, why = guard.decide(NOW, r, None)
    assert not go and why.startswith("freeze") and "Sydney to Hobart" in why
    r = [race("Sydney to Hobart", NOW - 30 * 60, "racing")]
    assert not guard.decide(NOW, r, None)[0]
    r = [race("Sydney to Hobart", NOW - 2 * H, "racing")]        # freeze over, cadence applies
    go, why = guard.decide(NOW, r, NOW - 25 * H)
    assert go and "under way" in why


def test_cadence_while_a_race_runs():
    r = [race("New York to Lorient", NOW - 4 * 24 * H, "racing")]
    go, why = guard.decide(NOW, r, NOW - 3 * H)
    assert not go and why.startswith("cadence") and "3.0 h ago" in why
    assert guard.decide(NOW, r, NOW - 21 * H)[0]
    assert guard.decide(NOW, r, None)[0]                        # unknown last release: deploy


def test_practice_course_never_holds_a_deploy():
    r = [race("Practice: Newport to Bermuda", NOW - 5 * 60, "racing", rolling=True)]
    assert guard.decide(NOW, r, NOW - 1 * H)[0]


def test_force_overrides_with_the_reason_kept():
    r = [race("Sydney to Hobart", NOW + 60 * 60, "upcoming")]
    go, why = guard.decide(NOW, r, None, force=True)
    assert go and why.startswith("freeze") and why.endswith("(forced)")


def test_parse_iso():
    assert guard.parse_iso("2026-09-06T18:16:25Z") == 1788718585
