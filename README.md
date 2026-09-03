# Virtual Navigator

Fantasy offshore racing: pick a race, register a virtual boat, and submit a
weather routing produced in your own navigation software. Every virtual boat
sails the **same polar** through the **same real weather**, and the leaderboard
ranks the armchair fleet alongside the **real boats on the tracker** — like
fantasy baseball, but your lineup is a set of waypoints.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/seed_demo.py   # optional: demo race + fleet
.venv/bin/python app.py                 # → http://127.0.0.1:5170
```

The demo seeds a Newport→Bermuda challenge that started 36 h ago, simulated
through real historical wind. Demo boats use PIN `0000`; the demo admin key is
`demo-admin`.

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
  (default 120) of stopped time. Wiggly routings are slow routings; the tack
  count shows in your boat panel. Because the boat always steers its best
  VMC toward the next waypoint, a wind shift that leaves a waypoint dead
  upwind (or dead downwind) never parks the boat — it automatically sails a
  tacking/gybing VMG course to get there.
- **Groundings hurt but don't sink you.** Real bathymetry (NOAA global DEM,
  cached on a ~500 m grid) is checked every step: in less water than the
  race's `grounding_depth_ft` (default 15 ft) the boat drags through at
  half speed. Shave the beach at your peril; steps spent aground show in
  your boat panel. Patches narrower than one sim step can be hopped over —
  it's a game, not a chart audit.
- **Exclusion zones drag like shoals.** Races can carry keep-out polygons
  (TSS boxes, ice limits, wildlife zones — importable straight from the
  linked YB tracker's course drawing). They're drawn red on the race map,
  and a virtual boat caught inside sails at half speed, same rule as
  grounding: inside is always slower than around. Steps spent in a zone
  show in your boat panel.
- **Soft course enforcement — no cheating, no DSQ.** Routings from your own
  software rarely land exactly on the race's marks. On submission the
  committee reconciles them: waypoints you're already standing on are
  skipped, and wherever the routing never passes within the mark radius of
  a required mark (start, roundings, finish), the mark itself is inserted
  into your route at the closest approach. You always sail the real course
  — there is nothing to gain by cutting a mark and nothing to be
  disqualified for when your finish line sits half a mile from ours. Every
  adjustment is reported back when you submit.
- **Currents set the fleet.** Real surface currents (Open-Meteo marine model,
  same 0.25° caching) push every boat — sailing or parked — so a Gulf Stream
  lane or a foul tide gate matters exactly like it does offshore. Optional
  per race (`currents_enabled`).
- **No time travel.** Boat state only ever advances. When you update your
  routing, the server first simulates your boat up to *now* under the old
  plan; everything sailed is locked, and only not-yet-reached waypoints are
  replaced. Late entries start at the line at submission time, never
  backdated. Every submission is timestamped in an audit log.
- **On-board information only.** The engine evaluates with *actual* wind in
  near-real time, so nobody — server included — knows the future. You plan
  with the same forecasts you'd carry on board; hindsight can't help you
  because the past is already sailed.
- **Private plans, public wakes.** Your future waypoints are visible only
  with your PIN. Your sailed track is public, like any race tracker.
- **On-board forecast archive.** Every 6 h the ticker snapshots the wind
  forecast over the race area — from the same model that will sail the boats —
  and stores it as a real **GRIB-1 file** (10 m U/V wind, 0–120 h, validated
  against an independent GRIB reader). Download it from the race page into
  qtVlm / Expedition / XyGrib and route with exactly what was knowable at the
  time; the archive is the honest record for post-race arguments.
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

## Accounts & roles

No PINs — proper navigator accounts (username + password, salted PBKDF2,
90-day session cookies). Two roles on one account type:

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

Boats created before accounts existed can be **claimed once** with their old
PIN from the race page. The per-race `admin_key` still works for scripted
admin API calls.

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

The integration surface is deliberately universal:

| Direction | Format | Works with |
|---|---|---|
| Race polar → your software | `.pol` text (TWA rows × TWS columns), from the race page | Expedition, Adrena, qtVlm, TimeZero, LuckGrib, OpenCPN weather_routing, PredictWind |
| Your routing → the game | GPX route/track, or CSV with lat/lon columns (decimal or `41 27.5 N` style) | anything that exports GPX/CSV |
| Your sailed track → your software | GPX track download per boat | anything that imports GPX |
| Real fleet → the game | GPX track or CSV `time,lat,lon` per boat (admin import) | YB/Yellowbrick viewer exports, expedition logs, AIS dumps |

## API sketch

```
GET  /api/races                      list races
POST /api/races                      create (returns admin_key)
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
POST /api/races/<id>/real_boats      add tracked real boat {admin_key, name, klass}
POST /api/real_boats/<id>/track      import tracker positions {admin_key, text}
POST /api/races/from_docs            auto-create a race from NoR/SI uploads
POST /api/races/<id>/docs            attach more documents (admin)
GET  /api/races/<id>/docs            list race documents
GET  /api/docs/<id>                  download a document
POST /api/races/<id>/yb              link a yb.tl race {admin_key, slug, model_filter?}
GET  /api/races/<id>/compare?real=&virtual=  gap split: boat speed / navigation / start
GET  /api/races/<id>/forecasts       list on-board forecast snapshots
GET  /api/forecasts/<id>.grb         download one snapshot as GRIB-1
```

## Working on the code

**Build features on a branch. Merge to `main` only when the feature is
finished, tested, and going out in the next deploy.**

This is not ceremony — `fly deploy` builds from the working directory, not
from `main`. Anything sitting unfinished in the tree ships the moment
someone deploys something else, and two half-done features editing the
same file turn every commit into a hand-separated diff.

```bash
git switch -c real-vs-virtual     # start the work
# … build and test it …
git switch main && git merge real-vs-virtual
fly deploy                        # main == what's live
```

Rules that follow from that:

- **One feature per branch**, so an unfinished feature can never be
  deployed by a change that has nothing to do with it.
- **Keep `main` deployable at all times.** `main` should always be safe to
  ship; if it isn't, the next person to deploy anything ships your bug.
- **Deploy from a clean tree.** Run `git status` before `fly deploy`. If it
  isn't clean, you don't know what you're about to put in front of the
  fleet — commit it, stash it, or switch branches first.
- **Verify against the live site, not the local file.** An edit that only
  exists on disk is not live. `curl` the deployed page (or open it) and
  confirm the change is actually there before calling it done.
- **Check `fly releases`** if prod doesn't match what you expect — someone
  else may have deployed since you last looked.

Races run continuously and the sim advances every minute, so a bad deploy
is sailed through and cannot be rewound. That is the reason for the care.

## Design notes & simplifications

- A background ticker advances every race in its active window (start − 72 h
  to start + 60 d) once a minute; state requests also catch up on demand, so
  the sim is correct even after a server restart.
- Boats hold the rhumb line to their next waypoint and "tack in place" at
  best VMC rather than sailing explicit zig-zags; a 3 % hysteresis keeps them
  from flip-flopping tacks, and each real tack/gybe costs the race's
  maneuver penalty.
- No land avoidance: routes crossing land will happily sail it, so route
  around headlands like you would offshore. Course marks are honoured within
  `mark_radius_nm`.
- Wind + surface current only (no waves or sail inventory limits) — the polar
  factor stands in for real-world degradation.
- Storage is SQLite (`data/vn.sqlite`), server is Flask; suitable for a club
  fleet, not the Vendée Globe's player count.
