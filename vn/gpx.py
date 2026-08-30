"""Route / track import from navigation software.

Every mainstream offshore routing package (Expedition, Adrena, qtVlm, TimeZero,
LuckGrib, PredictWind, OpenCPN...) can export a route as GPX; that is the
integration surface.  We also accept plain CSV (lat,lon per line, or columns
named lat/latitude and lon/longitude, e.g. an Expedition route export).
Timestamped GPX/CSV tracks are also parsed for real-boat position imports.
"""
import csv
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET


def _strip_ns(tag):
    return tag.rsplit("}", 1)[-1].lower()


def parse_route(text):
    """Return [(lat, lon), ...] from GPX or CSV text."""
    text = text.lstrip("﻿").strip()
    if text.startswith("<"):
        return _gpx_points(text, want_time=False)
    return _csv_points(text, want_time=False)


def parse_track(text):
    """Return [(t_unix, lat, lon), ...] from GPX or CSV text (times required)."""
    text = text.lstrip("﻿").strip()
    if text.startswith("<"):
        return _gpx_points(text, want_time=True)
    return _csv_points(text, want_time=True)


def _gpx_points(text, want_time):
    root = ET.fromstring(text)
    pts = []
    order = ["rtept", "trkpt", "wpt"]
    found = {k: [] for k in order}
    for el in root.iter():
        tag = _strip_ns(el.tag)
        if tag in found:
            try:
                lat, lon = float(el.get("lat")), float(el.get("lon"))
            except (TypeError, ValueError):
                continue
            t = None
            for child in el:
                if _strip_ns(child.tag) == "time" and child.text:
                    t = _parse_iso(child.text.strip())
            found[tag].append((t, lat, lon))
    for k in order:
        if found[k]:
            pts = found[k]
            break
    if want_time:
        return [(t, la, lo) for (t, la, lo) in pts if t is not None]
    return [(la, lo) for (_, la, lo) in pts]


def _csv_points(text, want_time):
    sample = text[:2000]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if h.startswith(n):
                    return i
        return None

    i_lat, i_lon = col("lat"), col("lon", "lng", "long")
    i_t = col("time", "date", "utc", "timestamp")
    body = rows[1:] if i_lat is not None else rows
    if i_lat is None:
        i_lat, i_lon, i_t = 0, 1, None
        if want_time and len(rows[0]) >= 3:
            i_t, i_lat, i_lon = 0, 1, 2

    out = []
    for r in body:
        try:
            lat = _coord(r[i_lat])
            lon = _coord(r[i_lon])
        except (ValueError, IndexError):
            continue
        if want_time:
            if i_t is None or i_t >= len(r):
                continue
            t = _parse_iso(r[i_t].strip())
            if t is None:
                continue
            out.append((t, lat, lon))
        else:
            out.append((lat, lon))
    return out


_COORD_RE = re.compile(
    r"^\s*(-?\d+(?:[.,]\d+)?)\s*°?\s*"             # degrees (may be decimal)
    r"(?:(\d+(?:[.,]\d+)?)\s*['′’]?\s*)?"          # minutes
    r"(?:(\d+(?:[.,]\d+)?)\s*[\"″”]?\s*)?"         # seconds
    r"([NSEW])?\s*$", re.I)


def parse_coord(s):
    """One coordinate in any common form: decimal degrees ('-71.5782'),
    decimal with hemisphere ('41.1754° N'), degrees-minutes
    ('40° 59.2' N', '073 32.3 W'), or degrees-minutes-seconds.
    A hemisphere letter wins over any sign on the number."""
    if isinstance(s, (int, float)):
        return float(s)
    m = _COORD_RE.match(str(s).strip())
    if not m or m.group(1) is None:
        raise ValueError(f"unreadable coordinate: {s!r}")
    deg = float(m.group(1).replace(",", "."))
    val = abs(deg)
    if m.group(2):
        val += float(m.group(2).replace(",", ".")) / 60.0
    if m.group(3):
        val += float(m.group(3).replace(",", ".")) / 3600.0
    hemi = (m.group(4) or "").upper()
    if hemi in ("S", "W"):
        return -val
    if hemi in ("N", "E"):
        return val
    return -val if deg < 0 else val


def _coord(s):
    return parse_coord(s)


def _parse_iso(s):
    s = s.replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = dt.datetime.strptime(s, "%m/%d/%Y %H:%M:%S")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def route_to_gpx(name, marks, desc=""):
    """marks: [{'name','lat','lon'}] -> a GPX 1.1 <rte> that Expedition,
    qtVlm, TimeZero, OpenCPN etc. import directly as a route."""
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    pts = "\n".join(
        f'    <rtept lat="{m["lat"]:.6f}" lon="{m["lon"]:.6f}">'
        f'<name>{esc(m["name"])}</name></rtept>'
        for m in marks)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Virtual Navigator" '
            'xmlns="http://www.topografix.com/GPX/1/1">\n'
            f'  <metadata><name>{esc(name)}</name><desc>{esc(desc)}</desc></metadata>\n'
            f'  <rte>\n    <name>{esc(name)}</name>\n{pts}\n  </rte>\n</gpx>\n')


def track_to_gpx(name, points):
    """points: [(t, lat, lon)] -> GPX 1.1 text for import into nav software."""
    esc = name.replace("&", "&amp;").replace("<", "&lt;")
    seg = []
    for t, lat, lon in points:
        iso = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        seg.append(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}"><time>{iso}</time></trkpt>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Virtual Navigator" '
            'xmlns="http://www.topografix.com/GPX/1/1">\n'
            f'  <trk><name>{esc}</name>\n    <trkseg>\n' + "\n".join(seg) +
            '\n    </trkseg>\n  </trk>\n</gpx>\n')
