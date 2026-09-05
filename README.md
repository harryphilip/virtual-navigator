# Virtual Navigator

Route the real race; sail the real weather. Pick an offshore race that is
actually on, enter a virtual boat, and submit a route, drawn on the chart or
exported from your own navigation software. Every virtual boat sails the
**same polar** through the **same real weather**, and the leaderboard ranks
the virtual fleet alongside the **real boats on the tracker**.

Live at https://virtual-navigator.fly.dev. Status: a single-server project
run by one committee for a club-sized fleet.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/seed_demo.py   # optional: demo race + fleet
.venv/bin/python app.py                 # → http://127.0.0.1:5170
```

The demo seeds a Newport→Bermuda challenge that started 36 h ago, simulated
through real historical wind.

## How the game works

- **One polar per race.** The race admin uploads the class polar (standard
  text format: TWS header row, one row per TWA — what Expedition, qtVlm,
  Adrena, ORC etc. read and write). A **performance factor** (default 90 %)
  derates it to realistic sailed performance. Competitors download the exact
  polar from the race page.
- **You submit a routing, the ocean sails it.** The engine advances each boat
  every `step_minutes` through real 10 m wind (Open-Meteo, cached on a 0.25°
  grid; a deterministic synthetic field fills in if offline). At each step the
  boat steers whatever heading maximises speed made good toward the next
  waypoint — so an upwind waypoint costs realistic tacking VMG — and boat speed
  comes from the shared polar. A background ticker advances every active race
  once a minute, so the fleet keeps sailing while nobody is watching.
- **Maneuvers cost time.** The boat holds its tack until the other tack is
  clearly faster; every tack or gybe then costs `maneuver_penalty_s` seconds
  (default 120) of stopped time. The tack count shows in your boat panel.
  Because the boat always steers its best VMC toward the next waypoint, a
  wind shift that leaves a waypoint dead upwind (or dead downwind) never
  parks the boat — it sails a tacking/gybing VMG course to get there.
- **Groundings hurt but don't sink you.** Real bathymetry (NOAA global DEM,
  cached on a ~500 m grid) is checked every step: in less water than the
  race's `grounding_depth_ft` (default 15 ft) the boat drags through at
  half speed. Steps spent aground show in your boat panel. Patches narrower
  than one sim step can be hopped over.
- **Exclusion zones drag like shoals.** Races can carry keep-out polygons
  (TSS boxes, ice limits, wildlife zones — importable straight from the
  linked YB tracker's course drawing). They're drawn red on the race map,
  and a virtual boat caught inside sails at half speed, same rule as
  grounding: inside is always slower than around. Steps spent in a zone
  show in your boat panel.
- **Soft course enforcement.** Routings from your own software rarely land
  exactly on the race's marks. On submission the server reconciles them:
  waypoints you're already standing on are skipped, and wherever the routing
  never passes within the mark radius of a required mark (start, roundings,
  finish), the mark itself is inserted into your route at the closest
  approach. Marks that must be left to port or starboard are checked for
  side too, by which way the mark's bearing sweeps along your leg. A pass on
  the wrong side, a touch-and-go at a turning mark, or a leg straight over
  the mark is rebuilt as a rounding half a mark radius off on the correct
  side. You always sail the real course, with nothing to gain by cutting a
  mark and no disqualification when your finish line sits half a mile from
  ours. Every adjustment is reported back when you submit.
- **Currents set the fleet.** Real surface currents (Open-Meteo marine model,
  same 0.25° caching) push every boat — sailing or parked — so a Gulf Stream
  lane or a foul tide gate matters exactly like it does offshore. Optional
  per race (`currents_enabled`).
- **No time travel.** Boat state only ever advances. When you update your
  routing, the server first simulates your boat up to *now* under the old
  plan; everything sailed is locked, and only not-yet-reached waypoints are
  replaced. Late entries start at the line at submission time, never
  backdated. Every submission is timestamped in an audit log.
- **Virtual boats start when the real fleet does.** In a race with a tracked
  real fleet, virtual boats wait on the line until 5% of the real boats have
  been seen under way after the gun (`fleet_start_pct`, `vn/fleetgate.py`),
  then start from that moment; a delayed start delays them with it. The
  decision is recorded once. Boats already sailing are never moved;
  `scripts/restart_boat.py` replays one from the fleet's start,
  `scripts/set_virtual_start.py` sets the start by hand when the tracker
  missed it, and `scripts/set_fleet_gate.py` changes the share or switches
  the gate off.
- **On-board information only.** The engine evaluates with *actual* wind in
  near-real time, so nobody, server included, knows the future. You plan
  with the same forecasts you'd carry on board, and the past is already
  sailed.
- **Private plans, public wakes.** Your future waypoints are visible only
  with your PIN. Your sailed track is public, like any race tracker.
- **On-board forecast archive.** Every 6 h the ticker snapshots the wind
  forecast over the race area — from the same model that will sail the boats —
  and stores it as a real **GRIB-1 file** (10 m U/V wind, 0–120 h, validated
  against an independent GRIB reader). Download it from the race page into
  qtVlm / Expedition / XyGrib and route with exactly what was known at the
  time; the archive is the record for post-race comparisons.
- **Combined leaderboard.** Virtual boats and imported real boats are ranked
  together by distance-to-finish along the course (finish order once home).
  Real boats carry a class label, with one-click filters such as
  *Class: Same polar (40ft)* to compare yourself against the boats actually
  sailing your polar.
- **Why the gap? Boat speed vs navigation.** Pick any tracked boat and any
  virtual boat on the race page and the time gap at equal progress (the
  leaderboard's distance-to-finish) is split three ways that sum exactly:
  *boat speed* — the real boat's own track re-sailed at race-polar speed
  through the same modelled wind, i.e. its "% of polar", also broken down
  by point of sail and wind band so an admin can see whether the polar or
  the performance factor is off; *navigation* — what is left once boat
  speed is removed: two polar boats on two routes, so wind found, miles
  sailed, tacks paid, and being there when the breeze was; and *start* for
  late entries. Historical tracks use only wind already in the cache;
  segments without data are assumed to sail at the boat's average % of
  polar and the coverage is reported. Without a virtual boat the card is a
  plain polar report for the real boat.
- **Live YB Tracking link.** Give a race the slug of a yb.tl tracker
  (optionally filtered to one class, e.g. `model_filter: "IMOCA"`) and the
  server imports the fleet roster + full track history, then polls for new
  positions every 10 minutes. Mark roundings, finishes and SOG are derived
  from the real tracks. (Verified against the Rolex Fastnet 2025 feed —
  the decoder speaks YB's binary AllPositions3/LatestPositions3 format.)
- **Live AIS link.** Races that run on AIS transponders instead of a
  tracker (the Vineyard Race) get their fleet from the event's scratch
  sheet (`scripts/link_ais.py` with a roster CSV such as
  `data/races/vineyard_2026_roster.csv`). With an `AISSTREAM_KEY` secret the
  server holds one aisstream.io websocket over the course area, binds each
  roster boat to the first sailing vessel broadcasting its name there, and
  folds positions in one a minute per boat. Sponsor suffixes and youth tags
  are ignored when matching; `scripts/set_mmsi.py` fixes a wrong or missing
  match, and every binding is written to the race log.

## Accounts & roles

Navigator accounts are a username and a password (salted PBKDF2, 90-day
session cookies). An email address is optional and used for one thing:
password-reset links (single use, one hour, every other session signed out
on change). Reset mail goes out over SMTP when the server has
`SMTP_HOST`, `SMTP_PORT` (587 STARTTLS or 465 TLS), `SMTP_USER`,
`SMTP_PASS` and `MAIL_FROM` set (`fly secrets set …`); any provider with an
SMTP endpoint works. `MAIL_BACKEND=console` logs the link instead (dev,
tests); with neither configured the reset form says so. Two roles on one
account type:

- **Navigators** register boats in races, submit routings, and get a public
  profile page (`/user?u=<name>`) showing every race they've sailed, results
  (finish time or distance to go), tacks, groundings, and the timestamped
  routing-submission log — the audit trail behind the no-time-travel rule.
- **Admins** additionally create and manage races (YB links, documents,
  tracker imports) — and race like anyone else; admin is a flag, not a
  separate account. The **first account registered on a fresh server becomes
  the admin**; admins can promote or demote others from profile pages (the
  last admin can't be removed), and `scripts/make_admin.py <username>` works
  from the server console as a recovery path.

Race management goes through admin accounts only.

## Auto-creating a race from the Notice of Race / SIs

Upload the official race documents (PDF or text) on the home page and a
virtual race is created automatically: name, start time (converted to UTC),
and the course marks are read from the documents, which are attached to the
race page for competitors. Two extractors:

- **Claude** (used when the server has Anthropic credentials — set
  `ANTHROPIC_API_KEY`, or sign in with `ant auth login`): the PDFs are read
  by `claude-opus-5` with a structured-output schema; it handles prose
  course descriptions, start schedules, mark tables and local time zones.
- **Built-in pattern parser** (offline fallback): reads coordinate tables in
  standard nav formats (`41° 27.20' N 071° 21.40' W` or decimal degrees) and
  start dates near the words "warning signal"/"first start".

Neither extractor invents positions — marks come out only if the documents
state them; if fewer than two marks are found the response returns the
partial extraction and the web form is prefilled for manual completion.
Admins can attach amendments later (`POST /api/races/<id>/docs`), and every
document is downloadable from the race page.

## Integrating with navigation software

Plain text formats throughout, so any routing software works:

| Direction | Format | Works with |
|---|---|---|
| Race polar → your software | `.pol` text (TWA rows × TWS columns), from the race page | Expedition, Adrena, qtVlm, TimeZero, LuckGrib, OpenCPN weather_routing, PredictWind |
| Your routing → the game | GPX route/track, or CSV with lat/lon columns (decimal or `41 27.5 N` style) | anything that exports GPX/CSV |
| Your sailed track → your software | GPX track download per boat | anything that imports GPX |
| Real fleet → the game | GPX track or CSV `time,lat,lon` per boat (admin import) | YB/Yellowbrick viewer exports, expedition logs, AIS dumps |
| Real fleet → the game, live | YB Tracking race (`scripts/link_yb.py`) or AIS via aisstream.io (`scripts/link_ais.py` + a scratch-sheet roster CSV, `AISSTREAM_KEY` secret) | races with a yb.tl page; races that require AIS transponders instead |

## API sketch

```
GET  /api/races                      list races
POST /api/races                      create (admin account)
GET  /api/races/<id>                 course + settings
GET  /api/races/<id>/polar           the polar file
GET  /api/races/<id>/state           leaderboard + fleet positions (advances the sim)
POST /api/auth/register|login|logout account endpoints (session cookie)
GET  /api/users/<name>               public navigator profile + history
POST /api/races/<id>/boats           register a virtual boat {name} (signed in)
POST /api/boats/<id>/route           submit/update routing {waypoints|gpx|csv} (owner)
GET  /api/boats/<id>                 owner view incl. private future route
POST /api/boats/<id>/claim           adopt a pre-account boat with its old PIN
GET  /api/boats/<id>/track.gpx       sailed track export
POST /api/races/<id>/real_boats      add tracked real boat {name, klass} (admin)
POST /api/real_boats/<id>/track      import tracker positions {text} (admin)
POST /api/races/from_docs            auto-create a race from NoR/SI uploads
POST /api/races/<id>/docs            attach more documents (admin)
GET  /api/races/<id>/docs            list race documents
GET  /api/docs/<id>                  download a document
POST /api/races/<id>/yb              link a yb.tl race {slug, model_filter?} (admin)
GET  /api/races/<id>/compare?real=&virtual=  gap split: boat speed / navigation / start
GET  /api/races/<id>/forecasts       list on-board forecast snapshots
GET  /api/forecasts/<id>.grb         download one snapshot as GRIB-1
```

## Working on the code

**Build features on a branch. Merge to `main` when the feature is finished
and the tests pass. Pushing `main` is the deploy.**

```bash
git switch -c real-vs-virtual     # start the work
# … build it, run the tests …
git switch main && git merge --no-ff real-vs-virtual
git push                          # tests run in CI, then main is deployed
```

Production (`virtual-navigator` on Fly.io) is deployed by one road only: the
GitHub Action in `.github/workflows/deploy.yml`, from `main`, after the test
suite passes, with a volume snapshot taken first. A hand `fly deploy` from a
laptop builds whatever happens to be in the working directory, skips the
tests, and races the Action; don't.

Something that can only be tried on a server (an ops script in `scripts/`,
a tracker link, a document import) goes to the **staging app** straight from
the branch, and is merged once it works there:

```bash
fly deploy --config fly.staging.toml     # virtual-navigator-staging
```

Rules that follow from that:

- **One feature per branch**, so an unfinished feature can never be
  deployed by a change that has nothing to do with it.
- **Branch again after merging.** Deleting the branch leaves you on `main`,
  and the next quick fix lands there without anyone noticing — which is
  exactly how stray commits reach `main`. `--no-ff` at least leaves a
  record; a fast-forward merge makes branch work and direct commits
  indistinguishable afterwards.
- **Keep `main` deployable at all times.** Every push to `main` ships; if
  `main` isn't safe to ship, the next person to merge anything ships your
  bug.
- **Freeze around a start.** From two hours before a race gun until the
  fleet has cleared the line, nothing goes to `main`. A deploy restarts the
  engine and replays the gap; the start is the worst moment for that.
- **Verify against the live site, not the local file.** An edit that only
  exists on disk is not live. Check `/healthz`, `curl` the deployed page (or
  open it) and confirm the change is actually there before calling it done.
- **One worktree per concurrent session.** Branches share a single checkout,
  so two sessions in the same directory are always on the same branch, and
  `git switch` in one moves the other. When someone else's uncommitted work
  is in the tree, work from `git worktree add ../vn-<feature> -b <feature>`
  instead of switching.
- **Check `fly releases`** and the Action log if prod doesn't match what
  you expect — someone else may have pushed since you last looked.

Races run continuously and the sim advances every minute, so a bad deploy
is sailed through and cannot be rewound. That is the reason for the care.

### Backups

The Action snapshots the Fly volume before every deploy. Fly also keeps
automatic daily snapshots for a few days. To restore, create a new volume
from a snapshot and attach it (`fly volumes snapshots list <vol>`, then
`fly volumes create vn_data --snapshot-id <id>`), and rehearse that once on
staging before you need it.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

`tests/` covers the polar, the geometry, the route and track parsers, course
reconciliation, the engine (sailed through mocked wind on a temporary
database) and the HTTP permission matrix. No test touches the network. Run
them before every merge; a case marked `xfail` documents a known defect that
is fixed on its own branch.

## Design notes & simplifications

- Leaflet 1.9.4 is vendored under `public/vendor/leaflet/` (BSD-2-Clause)
  so the pages do not depend on a CDN. Base map tiles come from the public
  OpenStreetMap server, whose usage policy tolerates small sites only; budget
  for a tile provider before a large fleet.

- A background ticker advances every race in its active window (start − 72 h
  to start + 60 d) once a minute, at most 24 h of steps per tick, so a gap
  after a restart is sailed over a few ticks while requests keep being served
  from stored state; without the ticker (tests, a shell) requests catch up
  on demand, so
  the sim is correct even after a server restart.
- Boats hold the rhumb line to their next waypoint and "tack in place" at
  best VMC rather than sailing explicit zig-zags; a 3 % hysteresis keeps them
  from flip-flopping tacks, and each real tack/gybe costs the race's
  maneuver penalty.
- No land avoidance: routes crossing land will happily sail it, so route
  around headlands like you would offshore. Course marks are honoured within
  `mark_radius_nm`; a mark's required side (`"side": "port"|"stbd"` in the
  race JSON, or `scripts/set_side.py` on a live race) is enforced on the
  submitted routing, not on the sailed track.
- Wind + surface current only (no waves or sail inventory limits) — the polar
  factor stands in for real-world degradation.
- Storage is SQLite (`data/vn.sqlite`), server is Flask; suitable for a club
  fleet, not the Vendée Globe's player count.

## License

MIT; see `LICENSE`. Leaflet (`public/vendor/leaflet/`) is BSD-2-Clause. Wind
and current data are Open-Meteo (CC BY 4.0); chart tiles are OpenStreetMap
and NOAA; polars are from meltemus.com; real-boat positions come from YB
Tracking and aisstream.io for comparison only.
