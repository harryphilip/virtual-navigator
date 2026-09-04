import pytest

from vn.sim import SETTING_RANGES, race_settings


def test_defaults_apply_when_keys_are_missing_or_blank():
    s = race_settings({"step_minutes": "", "perf_factor": None})
    assert s["step_minutes"] == 10 and s["perf_factor"] == 0.9
    assert s["mark_radius_nm"] == 2.0 and s["currents_enabled"] == 1
    assert set(s) == set(SETTING_RANGES) | {"currents_enabled"}


@pytest.mark.parametrize("key,value", [
    ("step_minutes", 0), ("step_minutes", 61), ("step_minutes", 2.5),
    ("mark_radius_nm", 0.0), ("mark_radius_nm", 25),
    ("perf_factor", 0.2), ("perf_factor", "fast"),
    ("maneuver_penalty_s", -5), ("grounding_depth_ft", 201),
])
def test_out_of_range_values_name_the_setting(key, value):
    with pytest.raises(ValueError) as e:
        race_settings({key: value})
    assert key in str(e.value)


def test_currents_flag_accepts_form_strings():
    assert race_settings({"currents_enabled": "false"})["currents_enabled"] == 0
    assert race_settings({"currents_enabled": "0"})["currents_enabled"] == 0
    assert race_settings({"currents_enabled": True})["currents_enabled"] == 1
    assert race_settings({})["currents_enabled"] == 1
