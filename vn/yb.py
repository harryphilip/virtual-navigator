"""YB Tracking (yb.tl) integration.

Race setup comes from the public JSON endpoint; positions come from the
binary AllPositions3/LatestPositions3 feeds.  The binary layout mirrors the
decoder in YB's own public race viewer: a flags byte, a uint32 reference
time, then per-team blocks of moments — a full record (uint32 dt, int32
lat/lon ×1e5, optional fields per flags), followed by delta records walking
*backwards* in time.
"""
import json
import struct
import urllib.request

BASE = "https://yb.tl"
UA = {"User-Agent": "VirtualNavigator/1.0 (fantasy race overlay)"}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def race_setup(slug):
    """Teams and metadata for a YB race. Returns dict with 'teams' list."""
    raw = _get(f"{BASE}/JSON/{slug}/RaceSetup")
    d = json.loads(raw.decode("utf-8", errors="replace"))
    teams = [{"yb_id": t.get("id"), "name": (t.get("name") or "").strip(),
              "model": (t.get("model") or "").strip(),
              "status": t.get("status", "")}
             for t in d.get("teams", []) if t.get("id") is not None]
    return {"title": d.get("title", slug), "teams": teams}


def zones(slug):
    """Keep-out polygons drawn on a YB race: [{'name', 'pts': [[lat, lon], ..]}].

    YB's RaceSetup carries them as poi lines flagged polygon=true (exclusion
    zones, TSS boxes, ice limits); plain lines (start/finish) are skipped.
    """
    raw = _get(f"{BASE}/JSON/{slug}/RaceSetup")
    d = json.loads(raw.decode("utf-8", errors="replace"))
    out = []
    for line in d.get("poi", {}).get("lines", []):
        if not line.get("polygon"):
            continue
        nums = [float(x) for x in (line.get("nodes") or "").split(",") if x]
        pts = [[nums[i], nums[i + 1]] for i in range(0, len(nums) - 1, 2)]
        if len(pts) >= 3:
            out.append({"name": (line.get("name") or "zone").strip(), "pts": pts})
    return out


def positions(slug, latest_only=False):
    """{yb_team_id: [(t_unix, lat, lon), ...]} sorted oldest→newest."""
    name = "LatestPositions3" if latest_only else "AllPositions3"
    return parse_positions(_get(f"{BASE}/BIN/{slug}/{name}"))


def parse_positions(buf):
    if len(buf) < 5:
        return {}
    flags = buf[0]
    has_alt = bool(flags & 1)
    has_dtf = bool(flags & 2)
    has_lap = bool(flags & 4)
    has_pc = bool(flags & 8)
    ref = struct.unpack_from(">I", buf, 1)[0]
    i = 5
    out = {}
    n = len(buf)
    while i + 4 <= n:
        team_id, count = struct.unpack_from(">HH", buf, i)
        i += 4
        prev = None
        pts = []
        for _ in range(count):
            if i >= n:
                break
            if buf[i] & 0x80:                     # delta record (back in time)
                w = struct.unpack_from(">H", buf, i)[0] & 0x7FFF
                dlat, dlon = struct.unpack_from(">hh", buf, i + 2)
                i += 6
                if has_alt:
                    i += 2
                if has_dtf:
                    i += 2
                    if has_lap:
                        i += 1
                if has_pc:
                    i += 2
                m = (prev[0] - w, prev[1] + dlat, prev[2] + dlon)
            else:                                 # full record
                dt, lat, lon = struct.unpack_from(">Iii", buf, i)
                i += 12
                if has_alt:
                    i += 2
                if has_dtf:
                    i += 4
                    if has_lap:
                        i += 1
                if has_pc:
                    i += 4
                m = (ref + dt, lat, lon)
            pts.append(m)
            prev = m
        out[team_id] = sorted(
            (t, lat / 1e5, lon / 1e5) for (t, lat, lon) in pts)
    return out
