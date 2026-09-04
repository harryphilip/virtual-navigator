import pytest

from vn.polar import Polar
from tests.conftest import POLAR_40FT, POLAR_CLASS40


def test_parses_tab_separated_with_label_cell():
    p = Polar.parse(POLAR_40FT)
    assert p.tws == [4, 6, 8, 10, 12, 14, 16, 20, 24, 28]
    assert p.twa[0] == 32 and p.twa[-1] == 180
    assert p.speed(90, 12) == pytest.approx(8.3)


def test_parses_class40_backslash_header():
    p = Polar.parse(POLAR_CLASS40)
    assert p.tws[0] == 0 and p.tws[-1] == 60
    assert p.speed(0, 20) == 0.0


@pytest.mark.parametrize("sep", [",", ";", " "])
def test_parses_other_separators(sep):
    text = "\n".join(sep.join(map(str, row)) for row in [
        ["TWA", 6, 12, 20], [45, 5.0, 7.0, 7.5], [90, 6.0, 8.0, 9.0], [150, 5.0, 8.0, 12.0]])
    p = Polar.parse(text)
    assert p.speed(90, 12) == pytest.approx(8.0)


def test_rows_sorted_by_twa_even_if_file_is_not():
    p = Polar.parse("TWA 10 20\n150 8 12\n60 7 8\n90 8 9\n")
    assert p.twa == [60, 90, 150]


def test_rejects_garbage():
    with pytest.raises(ValueError):
        Polar.parse("hello\nworld\n")
    with pytest.raises(ValueError):
        Polar.parse("TWA 10\n")


def test_interpolates_between_rows_and_columns():
    p = Polar.parse(POLAR_40FT)
    assert p.speed(80, 12) < p.speed(85, 12) < p.speed(90, 12)
    assert p.speed(90, 13) == pytest.approx((8.3 + 8.6) / 2)


def test_no_go_zone_tapers_to_zero_head_to_wind():
    p = Polar.parse(POLAR_40FT)
    assert p.speed(0, 12) == 0.0
    assert 0 < p.speed(16, 12) < p.speed(32, 12)


def test_twa_folds_and_tws_clamps():
    p = Polar.parse(POLAR_40FT)
    assert p.speed(270, 12) == p.speed(90, 12)
    assert p.speed(180, 100) == p.speed(180, 28)


def test_best_vmc_upwind_tacks_off_the_bearing():
    p = Polar.parse(POLAR_40FT)
    sides = p.best_vmc_by_side(bearing=0, twd_from=0, tws=12)
    for side in (1, -1):
        vmc, hdg, twa, bsp = sides[side]
        assert vmc > 3.0
        assert 30 <= twa <= 60             # close-hauled, not head to wind
        assert bsp > vmc
    # the two tacks mirror each other
    assert sides[1][2] == pytest.approx(sides[-1][2], abs=2)
    assert sides[1][1] != sides[-1][1]


def test_best_vmc_reaching_sails_the_rhumb_line():
    p = Polar.parse(POLAR_40FT)
    vmc, hdg, twa, bsp = p.best_vmc(bearing=90, twd_from=0, tws=12)
    assert hdg == pytest.approx(90, abs=6)
    assert vmc == pytest.approx(bsp, rel=0.02)


def test_performance_factor_scales_speed():
    p = Polar.parse(POLAR_40FT)
    full = p.best_vmc(90, 0, 12, factor=1.0)[0]
    derated = p.best_vmc(90, 0, 12, factor=0.9)[0]
    assert derated == pytest.approx(full * 0.9)
