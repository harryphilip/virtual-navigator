"""Polar file parsing and boat-speed interpolation.

Accepts the common text polar formats exported by Expedition, qtVlm, ORC and
most routing packages: a header row of true wind speeds (TWS, knots), then one
row per true wind angle (TWA, degrees) with boat speeds in knots.  Separators
may be tabs, spaces, commas or semicolons.  The top-left cell is ignored
(commonly "TWA", "twa/tws", "!", "Pol", etc.).
"""
import math
import re

from .geo import angle_diff


class Polar:
    def __init__(self, tws_list, twa_list, table, name="polar"):
        self.tws = tws_list          # ascending
        self.twa = twa_list          # ascending
        self.table = table           # table[i_twa][i_tws] -> bsp knots
        self.name = name

    # ---- parsing ----------------------------------------------------------
    @classmethod
    def parse(cls, text, name="polar"):
        rows = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "//", "!")):
                continue
            parts = [p for p in re.split(r"[,;\t ]+", line) if p]
            rows.append(parts)
        if len(rows) < 3:
            raise ValueError("polar needs a TWS header row and at least two TWA rows")

        header = rows[0]
        # first cell may be a label like "TWA" — drop anything non-numeric
        if not _is_num(header[0]):
            header = header[1:]
        tws = [float(x) for x in header if _is_num(x)]
        if len(tws) < 2:
            raise ValueError("could not read TWS header row of polar")

        twa, table = [], []
        for parts in rows[1:]:
            if not _is_num(parts[0]):
                continue
            a = float(parts[0])
            speeds = [float(x) if _is_num(x) else 0.0 for x in parts[1:1 + len(tws)]]
            if len(speeds) < len(tws):
                speeds += [speeds[-1] if speeds else 0.0] * (len(tws) - len(speeds))
            twa.append(a)
            table.append(speeds)
        if len(twa) < 2:
            raise ValueError("could not read any TWA rows of polar")

        order = sorted(range(len(twa)), key=lambda i: twa[i])
        twa = [twa[i] for i in order]
        table = [table[i] for i in order]
        return cls(tws, twa, table, name)

    # ---- interpolation ----------------------------------------------------
    def speed(self, twa, tws):
        """Boat speed (kn) at true wind angle twa (deg, 0..180) and TWS (kn)."""
        twa = abs(twa)
        if twa > 180.0:
            twa = 360.0 - twa
        tws = max(0.0, min(tws, self.tws[-1]))       # clamp above table max
        if twa < self.twa[0]:
            # inside the no-go zone: taper linearly to 0 at head-to-wind
            edge = self._interp_tws(0, tws)
            return edge * (twa / self.twa[0]) ** 2
        if twa > self.twa[-1]:
            return self._interp_tws(len(self.twa) - 1, tws)

        i = _bracket(self.twa, twa)
        s0 = self._interp_tws(i, tws)
        s1 = self._interp_tws(i + 1, tws)
        f = (twa - self.twa[i]) / (self.twa[i + 1] - self.twa[i])
        return s0 + f * (s1 - s0)

    def _interp_tws(self, i_twa, tws):
        row = self.table[i_twa]
        if tws <= self.tws[0]:
            return row[0] * (tws / self.tws[0]) if self.tws[0] > 0 else row[0]
        j = _bracket(self.tws, tws)
        f = (tws - self.tws[j]) / (self.tws[j + 1] - self.tws[j])
        return row[j] + f * (row[j + 1] - row[j])

    # ---- routing-relevant helpers -----------------------------------------
    def best_vmc_by_side(self, bearing, twd_from, tws, factor=1.0):
        """Best speed made good toward `bearing`, split by tack.

        The boat steers whatever heading maximises progress toward the next
        waypoint while its position is kept on the rhumb line to it.  Returns
        {side: (vmc_kn, heading, twa, bsp)} where side is +1 (wind over
        starboard) or -1 (wind over port) — so the engine can prefer staying
        on the current tack and charge a penalty when it crosses the wind.
        """
        best = {1: (0.0, bearing, angle_diff(bearing, twd_from), 0.0),
                -1: (0.0, bearing, angle_diff(bearing, twd_from), 0.0)}
        h = -89
        while h <= 89:
            hdg = (bearing + h) % 360.0
            signed = ((twd_from - hdg + 540.0) % 360.0) - 180.0
            side = 1 if signed >= 0 else -1
            twa = abs(signed)
            bsp = self.speed(twa, tws) * factor
            vmc = bsp * math.cos(math.radians(h))
            if vmc > best[side][0]:
                best[side] = (vmc, hdg, twa, bsp)
            h += 2
        return best

    def best_vmc(self, bearing, twd_from, tws, factor=1.0):
        """Best VMC regardless of tack: (vmc_kn, heading, twa, bsp)."""
        sides = self.best_vmc_by_side(bearing, twd_from, tws, factor)
        return max(sides.values(), key=lambda s: s[0])


def _is_num(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def _bracket(xs, x):
    """Index i such that xs[i] <= x <= xs[i+1]."""
    lo, hi = 0, len(xs) - 2
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid - 1
    return lo
