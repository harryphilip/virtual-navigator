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


def _coord(s):
    """Decimal degrees, or nav-style '41 27.5 N' / '41°27.5'S'."""
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    m = re.match(r"^\s*(\d+)[°\s]+([\d.]+)['\s]*([NSEW])\s*$", s, re.I)
    if not m:
        raise ValueError(s)
    val = float(m.group(1)) + float(m.group(2)) / 60.0
    return -val if m.group(3).upper() in "SW" else val


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
