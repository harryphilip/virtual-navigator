"""Live AIS positions for the real fleet, via aisstream.io.

Races without a YB tracker (the Vineyard Race asks each boat for an AIS
transponder instead) are followed here.  One websocket, keyed by the
AISSTREAM_KEY secret, subscribes to the bounding box of every race flagged
`ais` that is live.  Every position report inside a box is matched to that
race's real_boats roster (scripts/link_ais.py): by MMSI once one is known,
otherwise by the broadcast ship name — the first sailing/pleasure vessel
whose name matches an unbound roster entry is bound to it and logged.
scripts/set_mmsi.py overrides a wrong or missing match.  Positions go
through realfleet.ingest_points like YB points, at most one a minute per
boat.
"""
import datetime as dt
import json
import os
import re
import threading
import time

from .db import add_race_log, get_db
from .realfleet import ingest_points
from .sim import get_marks

URL = "wss://stream.aisstream.io/v0/stream"
POSITION_TYPES = ("PositionReport", "StandardClassBPositionReport",
                  "ExtendedClassBPositionReport")
STATIC_TYPES = ("ShipStaticData", "StaticDataReport")
SAILING_TYPES = (36, 37)          # AIS ship type: sailing, pleasure craft
MIN_POINT_GAP = 60                # seconds between stored points per boat
BOX_MARGIN_DEG = 0.5              # ~30 nm around the course
SESSION_SECONDS = 300             # reconnect this often to pick up race changes
PRE_START = 6 * 3600
POST_START = 10 * 86400


def normalize_name(s):
    """AIS names are upper-case, '@'-padded and cut at 20 characters."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").replace("@", " ").upper())


def roster_variants(name):
    """Forms a roster name might be broadcast under: 'Celebration (II)' →
    also 'Celebration'; 'Midnight Rider - PMP Strategy' → 'Midnight Rider';
    'SLEEPER YCC' → 'SLEEPER'."""
    out = [name]
    for sep in (" - ", "-", " (", " / ", " – "):
        if sep in name:
            out.append(name.split(sep)[0].strip())
    for v in list(out):
        if v.upper().endswith(" YCC"):        # Youth Challenge Cup tag
            out.append(v[:-4].strip())
    return out


def name_matches(ais_name, roster_name):
    raw = (ais_name or "").replace("@", " ").strip()
    a = normalize_name(raw)
    if len(a) < 3:
        return False
    for v in roster_variants(roster_name):
        r = normalize_name(v)
        if a == r or (len(raw) >= 20 and r.startswith(a)):
            return True
    return False


def parse_time(s):
    """aisstream's MetaData.time_utc: '2026-09-04 16:02:11.123456789 +0000 UTC'."""
    try:
        return int(dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
                   .replace(tzinfo=dt.timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def report_position(meta, body):
    """Where a position report puts the boat.  aisstream's MetaData spells it
    'latitude' / 'longitude' — lower case, unlike every other key in the
    envelope — while the typed body under Message spells it 'Latitude' /
    'Longitude'.  Read whichever is there; AIS 'not available' (91, 181)
    counts as no position."""
    for d, (ka, ko) in ((meta, ("latitude", "longitude")),
                        (meta, ("Latitude", "Longitude")),
                        (body, ("Latitude", "Longitude"))):
        lat, lon = d.get(ka), d.get(ko)
        if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
            return float(lat), float(lon)
    return None, None


def race_box(marks, margin=BOX_MARGIN_DEG):
    lats = [m["lat"] for m in marks]
    lons = [m["lon"] for m in marks]
    return [[min(lats) - margin, min(lons) - margin],
            [max(lats) + margin, max(lons) + margin]]


def live_ais_races(db, now=None):
    now = int(now or time.time())
    return [r for r in db.execute(
        "SELECT * FROM races WHERE ais=1 AND start_time-? <= ? AND ? <= start_time+?",
        (PRE_START, now, now, POST_START))]


class AISFeed(threading.Thread):
    def __init__(self, key):
        super().__init__(name="vn-ais", daemon=True)
        self.key = key
        self.types = {}            # mmsi -> AIS ship type, from static reports
        self.last_pt = {}          # rb_id -> t of last stored point
        self.rosters = {}          # race_id -> {"by_mmsi": {}, "unbound": [rows]}

    # ---- lifecycle -------------------------------------------------------
    def run(self):
        backoff = 5
        while True:
            try:
                db = get_db()
                races = live_ais_races(db)
                if not races:
                    time.sleep(60)
                    continue
                self._session(db, races)
                backoff = 5
            except Exception as e:
                # a rejected key closes the socket without a message; a
                # quiet box times out — either way, back off and reconnect
                print(f"[ais] {type(e).__name__}: {e} — retry in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)

    def _session(self, db, races):
        import websocket                      # websocket-client
        marks = {r["id"]: get_marks(db, r["id"]) for r in races}
        for r in races:
            self._load_roster(db, r["id"])
        sub = {"APIKey": self.key,
               "BoundingBoxes": [race_box(marks[r["id"]]) for r in races],
               "FilterMessageTypes": list(POSITION_TYPES + STATIC_TYPES)}
        ws = websocket.create_connection(URL, timeout=90)
        ws.send(json.dumps(sub))
        print(f"[ais] subscribed for race(s) {[r['id'] for r in races]}")
        started = time.time()
        n = 0
        try:
            while time.time() - started < SESSION_SECONDS:
                msg = json.loads(ws.recv())
                if "error" in msg:
                    raise RuntimeError(f"aisstream: {msg['error']}")
                n += self._handle(db, msg, races, marks)
        finally:
            ws.close()
            print(f"[ais] session closed, {n} point(s) stored")

    # ---- roster ----------------------------------------------------------
    def _load_roster(self, db, race_id):
        rows = db.execute("SELECT id, name, mmsi FROM real_boats WHERE race_id=?",
                          (race_id,)).fetchall()
        self.rosters[race_id] = {
            "by_mmsi": {r["mmsi"]: r["id"] for r in rows if r["mmsi"]},
            "unbound": [dict(r) for r in rows if not r["mmsi"]]}

    def _boat_for(self, db, race, mmsi, ship_name):
        ro = self.rosters[race["id"]]
        if mmsi in ro["by_mmsi"]:
            return ro["by_mmsi"][mmsi]
        if not ship_name or not ro["unbound"]:
            return None
        stype = self.types.get(mmsi)
        if stype is not None and stype not in SAILING_TYPES:
            return None
        for rb in ro["unbound"]:
            if name_matches(ship_name, rb["name"]):
                if mmsi in ro["by_mmsi"].values():
                    return None
                db.execute("UPDATE real_boats SET mmsi=? WHERE id=? AND mmsi IS NULL",
                           (mmsi, rb["id"]))
                add_race_log(db, race["id"],
                             f"AIS: '{rb['name']}' identified as MMSI {mmsi} "
                             f"(broadcast name '{ship_name.strip()}'). Wrong boat? "
                             "Tell the committee.")
                db.commit()
                ro["by_mmsi"][mmsi] = rb["id"]
                ro["unbound"].remove(rb)
                print(f"[ais] race {race['id']}: {rb['name']} = MMSI {mmsi}")
                return rb["id"]
        return None

    # ---- messages --------------------------------------------------------
    def _handle(self, db, msg, races, marks):
        mt = msg.get("MessageType")
        meta = msg.get("MetaData") or {}
        mmsi = meta.get("MMSI")
        if mmsi is None:
            return 0
        if mt in STATIC_TYPES:
            body = (msg.get("Message") or {}).get(mt) or {}
            stype = body.get("Type")
            if stype is None:
                stype = (body.get("ReportB") or {}).get("ShipType")
            if stype:
                self.types[mmsi] = int(stype)
            return 0
        if mt not in POSITION_TYPES:
            return 0
        lat, lon = report_position(meta, (msg.get("Message") or {}).get(mt) or {})
        if lat is None:
            return 0
        t = parse_time(meta.get("time_utc")) or int(time.time())
        stored = 0
        for race in races:
            box = race_box(marks[race["id"]])
            if not (box[0][0] <= lat <= box[1][0] and box[0][1] <= lon <= box[1][1]):
                continue
            rb_id = self._boat_for(db, race, mmsi, meta.get("ShipName"))
            if rb_id is None:
                continue
            if t - self.last_pt.get(rb_id, 0) < MIN_POINT_GAP:
                continue
            self.last_pt[rb_id] = t
            stored += ingest_points(db, race, marks[race["id"]], rb_id, [(t, lat, lon)])
        return stored


_feed = None


def start_feed():
    """Start the AIS thread once, if a key is configured."""
    global _feed
    if _feed is not None:
        return
    key = os.environ.get("AISSTREAM_KEY", "").strip()
    if not key:
        print("[ais] AISSTREAM_KEY not set — real-fleet AIS tracking is off")
        return
    _feed = AISFeed(key)
    _feed.start()
