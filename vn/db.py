import os
import sqlite3
import threading

_local = threading.local()
DB_PATH = os.environ.get("VN_DB", os.path.join(os.path.dirname(__file__), "..", "data", "vn.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,        -- lowercase handle
  display_name TEXT DEFAULT '',
  salt TEXT NOT NULL,
  pass_hash TEXT NOT NULL,
  is_admin INTEGER DEFAULT 0,           -- admins create/manage races AND race
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS races (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  start_time INTEGER NOT NULL,          -- unix seconds UTC
  perf_factor REAL DEFAULT 0.9,         -- fraction of polar the fleet achieves
  step_minutes INTEGER DEFAULT 10,
  mark_radius_nm REAL DEFAULT 2.0,
  polar_name TEXT DEFAULT 'polar',
  polar_text TEXT NOT NULL,
  admin_key TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  maneuver_penalty_s REAL DEFAULT 120,  -- time lost per tack/gybe
  currents_enabled INTEGER DEFAULT 1,
  yb_slug TEXT DEFAULT '',              -- linked YB tracker race, if any
  grounding_depth_ft REAL DEFAULT 15,   -- shallower than this: 50% speed
  created_by INTEGER,                   -- users.id of the creating admin
  zones_json TEXT DEFAULT '[]'          -- exclusion zones [{name, pts:[[lat,lon],..]}]
);
CREATE TABLE IF NOT EXISTS marks (
  race_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  name TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  side TEXT,                            -- 'port' | 'stbd' | NULL: side to leave it on
  PRIMARY KEY (race_id, seq)
);
CREATE TABLE IF NOT EXISTS boats (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  pin_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  sim_time INTEGER,                     -- boat state is valid up to this time
  lat REAL, lon REAL,
  next_mark INTEGER DEFAULT 1,          -- seq of next course mark to honour
  finished_at INTEGER,
  wind_side INTEGER,                    -- tack the boat is on (+1/-1)
  maneuvers INTEGER DEFAULT 0,          -- tacks + gybes so far
  groundings INTEGER DEFAULT 0,         -- sim steps spent in shallow water
  zone_steps INTEGER DEFAULT 0,         -- sim steps spent in exclusion zones
  owner_id INTEGER,                     -- users.id; legacy PIN boats until claimed
  UNIQUE (race_id, name)
);
CREATE TABLE IF NOT EXISTS route_wps (
  boat_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  passed INTEGER DEFAULT 0,
  PRIMARY KEY (boat_id, seq)
);
CREATE TABLE IF NOT EXISTS route_log (
  id INTEGER PRIMARY KEY,
  boat_id INTEGER NOT NULL,
  submitted_at INTEGER NOT NULL,
  wp_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS track (
  boat_id INTEGER NOT NULL,
  t INTEGER NOT NULL,
  lat REAL, lon REAL,
  twd REAL, tws REAL, bsp REAL, hdg REAL,
  PRIMARY KEY (boat_id, t)
);
CREATE TABLE IF NOT EXISTS real_boats (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  klass TEXT DEFAULT '',                -- e.g. same-polar class label
  yb_id INTEGER,                        -- team id on the YB tracker, if linked
  last_t INTEGER, last_lat REAL, last_lon REAL,
  sog REAL,
  next_mark INTEGER DEFAULT 1,
  finished_at INTEGER,
  UNIQUE (race_id, name)
);
CREATE TABLE IF NOT EXISTS real_track (
  rb_id INTEGER NOT NULL,
  t INTEGER NOT NULL,
  lat REAL, lon REAL,
  PRIMARY KEY (rb_id, t)
);
CREATE TABLE IF NOT EXISTS wind_cache (
  lat REAL, lon REAL, t INTEGER,
  twd REAL, tws REAL, source TEXT,
  PRIMARY KEY (lat, lon, t)
);
CREATE TABLE IF NOT EXISTS depth_cache (
  lat REAL, lon REAL, depth_m REAL,     -- elevation, negative = below sea level
  PRIMARY KEY (lat, lon)
);
CREATE TABLE IF NOT EXISTS current_cache (
  lat REAL, lon REAL, t INTEGER,
  cdir REAL, cspd REAL, source TEXT,    -- set toward cdir at cspd knots
  PRIMARY KEY (lat, lon, t)
);
CREATE TABLE IF NOT EXISTS race_docs (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL,
  kind TEXT DEFAULT 'doc',              -- nor | si | amendment | doc
  filename TEXT NOT NULL,
  mime TEXT DEFAULT 'application/octet-stream',
  content BLOB NOT NULL,
  uploaded_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_snapshots (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL,
  issued_at INTEGER NOT NULL,
  meta_json TEXT NOT NULL,
  grib BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS race_log (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL,
  at INTEGER NOT NULL,                  -- unix seconds UTC
  message TEXT NOT NULL                 -- committee action, plain language
);
"""

# additive migrations for databases created by earlier versions
MIGRATIONS = [
    "ALTER TABLE races ADD COLUMN maneuver_penalty_s REAL DEFAULT 120",
    "ALTER TABLE races ADD COLUMN currents_enabled INTEGER DEFAULT 1",
    "ALTER TABLE races ADD COLUMN yb_slug TEXT DEFAULT ''",
    "ALTER TABLE boats ADD COLUMN wind_side INTEGER",
    "ALTER TABLE boats ADD COLUMN maneuvers INTEGER DEFAULT 0",
    "ALTER TABLE boats ADD COLUMN groundings INTEGER DEFAULT 0",
    "ALTER TABLE races ADD COLUMN grounding_depth_ft REAL DEFAULT 15",
    "ALTER TABLE boats ADD COLUMN owner_id INTEGER",
    "ALTER TABLE races ADD COLUMN created_by INTEGER",
    "ALTER TABLE real_boats ADD COLUMN yb_id INTEGER",
    "ALTER TABLE real_boats ADD COLUMN last_t INTEGER",
    "ALTER TABLE real_boats ADD COLUMN last_lat REAL",
    "ALTER TABLE real_boats ADD COLUMN last_lon REAL",
    "ALTER TABLE real_boats ADD COLUMN sog REAL",
    "ALTER TABLE real_boats ADD COLUMN next_mark INTEGER DEFAULT 1",
    "ALTER TABLE real_boats ADD COLUMN finished_at INTEGER",
    "ALTER TABLE races ADD COLUMN zones_json TEXT DEFAULT '[]'",
    "ALTER TABLE boats ADD COLUMN zone_steps INTEGER DEFAULT 0",
    "ALTER TABLE marks ADD COLUMN side TEXT",
]


def get_db():
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass                      # column already exists
        conn.commit()
        _local.conn = conn
    return conn


def add_race_log(db, race_id, message, at=None):
    """Append a committee-log entry — every course/zone/routing change goes
    here so competitors can see what happened, when, and why. Caller commits."""
    import time as _time
    db.execute("INSERT INTO race_log(race_id, at, message) VALUES (?,?,?)",
               (race_id, int(at or _time.time()), message))
