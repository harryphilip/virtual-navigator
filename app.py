"""Virtual Navigator — fantasy offshore-racing server.

Run:  .venv/bin/python app.py   (serves on http://127.0.0.1:5170)
"""
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import threading
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory, Response
from werkzeug.middleware.proxy_fix import ProxyFix

from vn import yb
from vn.compare import CompareError, compare
from vn.db import add_race_log, get_db
from vn.forecast import make_snapshot
from vn.nor import extract_race, MAX_DOC_BYTES
from vn.gpx import parse_coord, parse_route, parse_track, route_to_gpx, track_to_gpx
from vn.polar import Polar
from vn.realfleet import ingest_points, recompute
from vn.sim import (catch_up_race, dtf_nm, enforce_course, get_marks,
                    race_polar, race_zones)
from vn.wind import wind_health

app = Flask(__name__, static_folder="public", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)   # Fly's TLS proxy

TRACK_MAX_POINTS = 400
SESSION_COOKIE = "vn_session"
SESSION_DAYS = 90


# ---------- helpers ---------------------------------------------------------

def _hash_pin(pin):
    """Legacy PIN hash — kept only so pre-account boats can be claimed."""
    return hashlib.sha256(("vn-salt:" + pin).encode()).hexdigest()


def _hash_pw(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode(),
                               bytes.fromhex(salt_hex), 200_000).hex()


def current_user(db):
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    return db.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token=? AND s.expires_at>?", (tok, int(time.time()))).fetchone()


def _start_session(resp, db, user_id):
    tok = secrets.token_hex(32)
    now = int(time.time())
    db.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) "
               "VALUES (?,?,?,?)", (tok, user_id, now, now + SESSION_DAYS * 86400))
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    db.commit()
    resp.set_cookie(SESSION_COOKIE, tok, max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


def _user_json(u):
    return {"username": u["username"], "display_name": u["display_name"] or u["username"],
            "is_admin": bool(u["is_admin"])}


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _parse_time(s):
    if isinstance(s, (int, float)):
        return int(s)
    d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def _race_or_404(db, race_id):
    return db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()


def _decimate(rows, cap=TRACK_MAX_POINTS):
    if len(rows) <= cap:
        return rows
    stride = len(rows) / float(cap)
    out = [rows[int(i * stride)] for i in range(cap)]
    out[-1] = rows[-1]
    return out


# ---------- pages -----------------------------------------------------------

@app.get("/")
def home():
    return send_from_directory("public", "index.html")


@app.get("/race")
def race_page():
    return send_from_directory("public", "race.html")


@app.get("/user")
def user_page():
    return send_from_directory("public", "user.html")


# ---------- accounts ---------------------------------------------------------

@app.post("/api/auth/register")
def auth_register():
    db = get_db()
    d = request.get_json(force=True)
    username = (d.get("username") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,29}", username):
        return _err("username: 3-30 chars, letters/digits/dash/underscore")
    password = d.get("password") or ""
    if len(password) < 6:
        return _err("password must be at least 6 characters")
    salt = secrets.token_hex(16)
    first = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
    try:
        cur = db.execute(
            "INSERT INTO users(username,display_name,salt,pass_hash,is_admin,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (username, (d.get("display_name") or "").strip()[:60], salt,
             _hash_pw(password, salt), 1 if first else 0, int(time.time())))
    except Exception:
        return _err("that username is taken", 409)
    db.commit()
    u = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    resp = jsonify({"ok": True, "user": _user_json(u), "first_admin": first})
    return _start_session(resp, db, u["id"])


@app.post("/api/auth/login")
def auth_login():
    db = get_db()
    d = request.get_json(force=True)
    u = db.execute("SELECT * FROM users WHERE username=?",
                   ((d.get("username") or "").strip().lower(),)).fetchone()
    if not u or _hash_pw(d.get("password") or "", u["salt"]) != u["pass_hash"]:
        return _err("wrong username or password", 403)
    resp = jsonify({"ok": True, "user": _user_json(u)})
    return _start_session(resp, db, u["id"])


@app.post("/api/auth/logout")
def auth_logout():
    db = get_db()
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        db.execute("DELETE FROM sessions WHERE token=?", (tok,))
        db.commit()
    resp = jsonify({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/auth/me")
def auth_me():
    u = current_user(get_db())
    return jsonify({"user": _user_json(u) if u else None})


@app.get("/api/users/<username>")
def user_profile(username):
    """Public navigator profile: every boat they've raced, with results and
    the full routing-submission history."""
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE username=?",
                   (username.strip().lower(),)).fetchone()
    if not u:
        return _err("no such navigator", 404)
    boats = []
    for b in db.execute(
            "SELECT b.*, r.name race_name, r.start_time FROM boats b "
            "JOIN races r ON r.id = b.race_id WHERE b.owner_id=? "
            "ORDER BY r.start_time DESC", (u["id"],)):
        marks = get_marks(db, b["race_id"])
        subs = db.execute(
            "SELECT submitted_at, wp_json FROM route_log WHERE boat_id=? "
            "ORDER BY submitted_at DESC LIMIT 12", (b["id"],)).fetchall()
        boats.append({
            "boat_id": b["id"], "boat_name": b["name"],
            "race_id": b["race_id"], "race_name": b["race_name"],
            "race_start": b["start_time"],
            "started": b["sim_time"] is not None,
            "finished_at": b["finished_at"],
            "dtf": dtf_nm(b["lat"], b["lon"], marks, b["next_mark"])
                   if b["lat"] is not None else None,
            "maneuvers": b["maneuvers"] or 0, "groundings": b["groundings"] or 0,
            "submissions": [{"at": s["submitted_at"],
                             "n": len(json.loads(s["wp_json"]))} for s in subs],
        })
    viewer = current_user(db)
    return jsonify({"user": _user_json(u), "joined": u["created_at"], "boats": boats,
                    "viewer_is_admin": bool(viewer and viewer["is_admin"]),
                    "is_self": bool(viewer and viewer["id"] == u["id"])})


@app.post("/api/users/<username>/admin")
def set_admin(username):
    """Admins can grant or revoke the admin role (never the last admin)."""
    db = get_db()
    actor = current_user(db)
    if not (actor and actor["is_admin"]):
        return _err("admin access required", 403)
    d = request.get_json(force=True)
    target = db.execute("SELECT * FROM users WHERE username=?",
                        (username.strip().lower(),)).fetchone()
    if not target:
        return _err("no such user", 404)
    make_admin = 1 if d.get("admin") else 0
    if not make_admin:
        admins = db.execute("SELECT COUNT(*) c FROM users WHERE is_admin=1").fetchone()["c"]
        if admins <= 1 and target["is_admin"]:
            return _err("cannot remove the last admin", 409)
    db.execute("UPDATE users SET is_admin=? WHERE id=?", (make_admin, target["id"]))
    db.commit()
    return jsonify({"ok": True, "username": target["username"], "is_admin": bool(make_admin)})


# ---------- races -----------------------------------------------------------

@app.get("/api/races")
def list_races():
    db = get_db()
    now = int(time.time())
    out = []
    for r in db.execute("SELECT * FROM races ORDER BY start_time DESC"):
        nb = db.execute("SELECT COUNT(*) c FROM boats WHERE race_id=?", (r["id"],)).fetchone()["c"]
        nr = db.execute("SELECT COUNT(*) c FROM real_boats WHERE race_id=?", (r["id"],)).fetchone()["c"]
        out.append({"id": r["id"], "name": r["name"], "description": r["description"],
                    "start_time": r["start_time"], "started": now >= r["start_time"],
                    "virtual_boats": nb, "real_boats": nr,
                    "polar_name": r["polar_name"], "perf_factor": r["perf_factor"]})
    return jsonify(out)


@app.post("/api/races")
def create_race():
    db = get_db()
    u = current_user(db)
    if not (u and u["is_admin"]):
        return _err("only admin accounts can create races — sign in as an admin", 403)
    d = request.get_json(force=True)
    try:
        name = d["name"].strip()
        start = _parse_time(d["start_time"])
        polar_text = d["polar_text"]
        assert name, "race name is required"
        polar = Polar.parse(polar_text)
    except (KeyError, AssertionError, ValueError, TypeError) as e:
        return _err(f"invalid race definition: {e}")
    marks = []
    for i, m in enumerate(d.get("marks", [])):
        try:
            lat, lon = parse_coord(m["lat"]), parse_coord(m["lon"])
            assert -90 <= lat <= 90 and -180 <= lon <= 180
        except (KeyError, ValueError, AssertionError, TypeError):
            return _err(f"mark {i + 1} ({m.get('name', '?')}): could not read "
                        f"position {m.get('lat')!r}, {m.get('lon')!r} — use "
                        "decimal degrees or degrees-minutes like 41° 27.5' N")
        marks.append({"name": m.get("name", f"Mark {i}"), "lat": lat, "lon": lon})
    if len(marks) < 2:
        return _err("at least two marks (start and finish) are required")
    admin_key = secrets.token_hex(12)
    cur = db.execute(
        "INSERT INTO races(name,description,start_time,perf_factor,step_minutes,"
        "mark_radius_nm,polar_name,polar_text,admin_key,created_at,"
        "maneuver_penalty_s,currents_enabled,grounding_depth_ft) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, d.get("description", ""), start,
         float(d.get("perf_factor", 0.9)), int(d.get("step_minutes", 10)),
         float(d.get("mark_radius_nm", 2.0)),
         d.get("polar_name", "race polar"), polar_text, admin_key, int(time.time()),
         float(d.get("maneuver_penalty_s", 120)),
         1 if d.get("currents_enabled", True) else 0,
         float(d.get("grounding_depth_ft", 15))))
    db.execute("UPDATE races SET created_by=? WHERE id=?", (u["id"], cur.lastrowid))
    race_id = cur.lastrowid
    for i, m in enumerate(marks):
        db.execute("INSERT INTO marks(race_id,seq,name,lat,lon) VALUES (?,?,?,?,?)",
                   (race_id, i, m["name"], m["lat"], m["lon"]))
    db.commit()
    return jsonify({"id": race_id,
                    "polar_tws": polar.tws, "polar_twa": polar.twa})


@app.get("/api/races/<int:race_id>")
def race_detail(race_id):
    db = get_db()
    r = _race_or_404(db, race_id)
    if not r:
        return _err("race not found", 404)
    marks = [{"seq": m["seq"], "name": m["name"], "lat": m["lat"], "lon": m["lon"]}
             for m in get_marks(db, race_id)]
    return jsonify({"id": r["id"], "name": r["name"], "description": r["description"],
                    "start_time": r["start_time"], "perf_factor": r["perf_factor"],
                    "step_minutes": r["step_minutes"], "mark_radius_nm": r["mark_radius_nm"],
                    "polar_name": r["polar_name"], "marks": marks,
                    "maneuver_penalty_s": r["maneuver_penalty_s"],
                    "currents_enabled": bool(r["currents_enabled"]),
                    "grounding_depth_ft": r["grounding_depth_ft"],
                    "zones": race_zones(r),
                    "yb_slug": r["yb_slug"] or ""})


@app.get("/api/races/<int:race_id>/polar")
def race_polar_text(race_id):
    db = get_db()
    r = _race_or_404(db, race_id)
    if not r:
        return _err("race not found", 404)
    return Response(r["polar_text"], mimetype="text/plain",
                    headers={"Content-Disposition":
                             f'attachment; filename="race{race_id}_polar.pol"'})


@app.get("/api/overview")
def overview():
    """Light snapshot for the home page: every race with its course line,
    status, and a podium of the current standings (the ticker keeps the sim
    fresh, so this reads straight from stored state)."""
    db = get_db()
    now = int(time.time())
    out = []
    for r in db.execute("SELECT * FROM races ORDER BY start_time DESC"):
        marks = get_marks(db, r["id"])
        course_len = dtf_nm(marks[0]["lat"], marks[0]["lon"], marks, 1) if len(marks) > 1 else 0
        entries = []
        for b in db.execute("SELECT * FROM boats WHERE race_id=?", (r["id"],)):
            if b["sim_time"] is None:
                continue
            entries.append({
                "name": b["name"], "type": "virtual",
                "lat": b["lat"], "lon": b["lon"],
                "finished_at": b["finished_at"],
                "dtf": dtf_nm(b["lat"], b["lon"], marks, b["next_mark"])})
        for rb in db.execute(
                "SELECT * FROM real_boats WHERE race_id=? AND last_t IS NOT NULL",
                (r["id"],)):
            entries.append({
                "name": rb["name"], "type": "real",
                "lat": rb["last_lat"], "lon": rb["last_lon"],
                "finished_at": rb["finished_at"],
                "dtf": dtf_nm(rb["last_lat"], rb["last_lon"], marks, rb["next_mark"])})
        entries.sort(key=lambda e: (0, e["finished_at"]) if e["finished_at"]
                     else (1, e["dtf"]))
        racing = [e for e in entries if not e["finished_at"]]
        if now < r["start_time"]:
            status = "upcoming"
        elif entries and (not racing or now > r["start_time"] + 45 * 86400):
            status = "finished"       # done, or dormant (retirees never finish)
        else:
            status = "racing"
        out.append({
            "id": r["id"], "name": r["name"], "description": r["description"],
            "start_time": r["start_time"], "status": status,
            "course_len_nm": round(course_len),
            "polar_name": r["polar_name"],
            "marks": [[m["lat"], m["lon"]] for m in marks],
            "entries_total": len(entries),
            "top": [{"name": e["name"], "type": e["type"],
                     "dtf": round(e["dtf"]), "finished": bool(e["finished_at"])}
                    for e in entries[:3]],
            "leaders": [[e["lat"], e["lon"]] for e in entries[:5]
                        if e["lat"] is not None and not e["finished_at"]],
        })
    order = {"racing": 0, "upcoming": 1, "finished": 2}
    out.sort(key=lambda r: (order[r["status"]], r["start_time"]))
    return jsonify(out)


@app.get("/api/races/<int:race_id>/course.gpx")
def race_course_gpx(race_id):
    """The race course as a GPX route — the starting point for your first
    routing run: import it, route between the marks, export, upload."""
    db = get_db()
    r = _race_or_404(db, race_id)
    if not r:
        return _err("race not found", 404)
    marks = [dict(m) for m in get_marks(db, race_id)]
    stamp = dt.datetime.fromtimestamp(r["start_time"], dt.timezone.utc)
    gpx = route_to_gpx(
        r["name"], marks,
        desc=f"Start {stamp.strftime('%Y-%m-%d %H:%M UTC')} — course marks in "
             f"sailing order; pass within {r['mark_radius_nm']} nm of each.")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", r["name"])[:40]
    return Response(gpx, mimetype="application/gpx+xml",
                    headers={"Content-Disposition":
                             f'attachment; filename="{safe}_course.gpx"'})


@app.get("/api/races/<int:race_id>/state")
def race_state(race_id):
    db = get_db()
    r = _race_or_404(db, race_id)
    if not r:
        return _err("race not found", 404)
    now = int(time.time())
    catch_up_race(db, race_id, now)
    marks = get_marks(db, race_id)
    course_len = dtf_nm(marks[0]["lat"], marks[0]["lon"], marks, 1) if len(marks) > 1 else 0

    owners = {r["id"]: r["username"] for r in db.execute("SELECT id, username FROM users")}
    entries = []
    for b in db.execute("SELECT * FROM boats WHERE race_id=?", (race_id,)):
        trk = db.execute("SELECT t,lat,lon,bsp FROM track WHERE boat_id=? ORDER BY t",
                         (b["id"],)).fetchall()
        last = trk[-1] if trk else None
        has_route = db.execute(
            "SELECT COUNT(*) c FROM route_wps WHERE boat_id=? AND passed=0",
            (b["id"],)).fetchone()["c"] > 0
        entries.append({
            "type": "virtual", "id": b["id"], "name": b["name"], "klass": "virtual",
            "lat": b["lat"], "lon": b["lon"],
            "sog": last["bsp"] if last else None,
            "dtf": dtf_nm(b["lat"], b["lon"], marks, b["next_mark"]) if b["lat"] is not None else course_len,
            "finished_at": b["finished_at"], "started": b["sim_time"] is not None,
            "has_route": has_route, "maneuvers": b["maneuvers"] or 0,
            "owner": owners.get(b["owner_id"]),
            "track": [[p["lat"], p["lon"]] for p in _decimate(trk)],
        })
    for rb in db.execute("SELECT * FROM real_boats WHERE race_id=?", (race_id,)):
        trk = db.execute(
            "SELECT lat,lon FROM real_track WHERE rb_id=? ORDER BY t DESC LIMIT 300",
            (rb["id"],)).fetchall()
        trk.reverse()
        started = rb["last_t"] is not None
        entries.append({
            "type": "real", "id": rb["id"], "name": rb["name"],
            "klass": rb["klass"] or "real",
            "lat": rb["last_lat"], "lon": rb["last_lon"],
            "sog": rb["sog"],
            "dtf": dtf_nm(rb["last_lat"], rb["last_lon"], marks, rb["next_mark"])
                   if started else course_len,
            "finished_at": rb["finished_at"],
            "started": started, "has_route": None, "maneuvers": None,
            "track": [[p["lat"], p["lon"]] for p in _decimate(trk)],
        })

    def sort_key(e):
        if e["finished_at"]:
            return (0, e["finished_at"])
        if not e["started"]:
            return (2, e["dtf"])
        return (1, e["dtf"])
    entries.sort(key=sort_key)
    for i, e in enumerate(entries):
        e["rank"] = i + 1

    return jsonify({"now": now, "start_time": r["start_time"],
                    "course_len_nm": course_len,
                    "marks": [{"name": m["name"], "lat": m["lat"], "lon": m["lon"]}
                              for m in marks],
                    "weather": wind_health(db, now),
                    "entries": entries})


# ---------- race documents & auto-creation ----------------------------------

def _doc_kind(filename):
    f = filename.lower()
    if "nor" in f or "notice" in f:
        return "nor"
    if f.startswith("si") or "sailing" in f or "instruction" in f:
        return "si"
    if "amend" in f:
        return "amendment"
    return "doc"


def _read_uploads(req):
    """Uploaded race documents as [(filename, mime, bytes)]."""
    docs = []
    for f in req.files.getlist("docs"):
        data = f.read()
        if not data:
            continue
        if len(data) > MAX_DOC_BYTES:
            raise ValueError(f"{f.filename} exceeds 15 MB")
        docs.append((f.filename or "document",
                     f.mimetype or "application/octet-stream", data))
    if req.form.get("text"):
        docs.append(("pasted.txt", "text/plain", req.form["text"].encode()))
    return docs


@app.post("/api/races/from_docs")
def race_from_docs():
    """Upload a Notice of Race / Sailing Instructions and auto-create the
    virtual race.  The documents are attached to the race for competitors.

    Multipart form: docs (files, PDF or text, repeatable), text (pasted),
    optional polar_text/polar_name, perf_factor, maneuver_penalty_s.
    Returns 422 with the partial extraction when the course could not be
    determined, so the client can prefill the manual form instead.
    """
    db = get_db()
    u = current_user(db)
    if not (u and u["is_admin"]):
        return _err("only admin accounts can create races — sign in as an admin", 403)
    try:
        docs = _read_uploads(request)
    except ValueError as e:
        return _err(str(e))
    if not docs:
        return _err("attach at least one document (PDF or text)")

    ex = extract_race(docs)
    warnings = list(ex["warnings"])

    if len(ex["marks"]) < 2:
        return jsonify({"error": "could not extract a usable course",
                        "extract": ex}), 422

    start = None
    if ex["start_time_utc"]:
        try:
            start = _parse_time(ex["start_time_utc"])
        except ValueError:
            warnings.append(f"unparseable start time {ex['start_time_utc']!r}")
    if start is None:
        start = (int(time.time()) // 3600 + 7 * 24) * 3600
        warnings.append("start time not found — set to one week from now; "
                        "edit before racing")

    polar_text = request.form.get("polar_text", "").strip()
    polar_name = request.form.get("polar_name", "").strip()
    if polar_text:
        try:
            Polar.parse(polar_text)
        except ValueError as e:
            return _err(f"invalid polar: {e}")
    else:
        polar_text = open(os.path.join(os.path.dirname(__file__),
                                       "data", "polar_40ft.pol")).read()
        polar_name = polar_name or "Generic 40ft offshore (default)"
        warnings.append("no polar supplied — using the generic 40 ft polar; "
                        "replace it if the fleet sails something else")

    desc_bits = [b for b in (ex["organizer"], ex["course_description"]) if b]
    if ex["classes"]:
        desc_bits.append("Classes: " + ", ".join(ex["classes"][:8]))
    if ex["distance_nm"]:
        desc_bits.append(f"~{round(ex['distance_nm'])} nm")
    desc_bits.append(f"Auto-created from race documents ({ex['extractor']}).")

    now = int(time.time())
    admin_key = secrets.token_hex(12)
    cur = db.execute(
        "INSERT INTO races(name,description,start_time,perf_factor,step_minutes,"
        "mark_radius_nm,polar_name,polar_text,admin_key,created_at,"
        "maneuver_penalty_s,currents_enabled,grounding_depth_ft) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ((ex["name"] or "Imported race")[:90], " — ".join(desc_bits), start,
         float(request.form.get("perf_factor", 0.9)), 10, 2.0,
         polar_name or "race polar", polar_text, admin_key, now,
         float(request.form.get("maneuver_penalty_s", 120)), 1, 15.0))
    race_id = cur.lastrowid
    db.execute("UPDATE races SET created_by=? WHERE id=?", (u["id"], race_id))
    for i, m in enumerate(ex["marks"][:50]):
        db.execute("INSERT INTO marks(race_id,seq,name,lat,lon) VALUES (?,?,?,?,?)",
                   (race_id, i, m["name"] or f"Mark {i}", m["lat"], m["lon"]))
    for fname, mime, data in docs:
        db.execute("INSERT INTO race_docs(race_id,kind,filename,mime,content,"
                   "uploaded_at) VALUES (?,?,?,?,?,?)",
                   (race_id, _doc_kind(fname), fname, mime, data, now))
    db.commit()
    return jsonify({"id": race_id,
                    "name": ex["name"], "start_time": start,
                    "marks": len(ex["marks"]), "extractor": ex["extractor"],
                    "warnings": warnings})


@app.post("/api/races/<int:race_id>/docs")
def add_race_doc(race_id):
    """Attach further documents (SIs, amendments) to an existing race."""
    db = get_db()
    _, err = _auth_admin(db, race_id)
    if err:
        return err
    try:
        docs = _read_uploads(request)
    except ValueError as e:
        return _err(str(e))
    if not docs:
        return _err("no documents attached")
    now = int(time.time())
    for fname, mime, data in docs:
        db.execute("INSERT INTO race_docs(race_id,kind,filename,mime,content,"
                   "uploaded_at) VALUES (?,?,?,?,?,?)",
                   (race_id, request.form.get("kind") or _doc_kind(fname),
                    fname, mime, data, now))
    db.commit()
    return jsonify({"ok": True, "docs": len(docs)})


@app.get("/api/races/<int:race_id>/docs")
def list_race_docs(race_id):
    db = get_db()
    rows = db.execute(
        "SELECT id, kind, filename, mime, LENGTH(content) size, uploaded_at "
        "FROM race_docs WHERE race_id=? ORDER BY uploaded_at", (race_id,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/docs/<int:doc_id>")
def download_doc(doc_id):
    db = get_db()
    row = db.execute("SELECT * FROM race_docs WHERE id=?", (doc_id,)).fetchone()
    if not row:
        return _err("document not found", 404)
    return Response(row["content"], mimetype=row["mime"],
                    headers={"Content-Disposition":
                             f'attachment; filename="{row["filename"]}"'})


# ---------- virtual boats & routings ---------------------------------------

@app.post("/api/races/<int:race_id>/boats")
def register_boat(race_id):
    db = get_db()
    if not _race_or_404(db, race_id):
        return _err("race not found", 404)
    u = current_user(db)
    if not u:
        return _err("sign in to register a boat", 401)
    d = request.get_json(force=True)
    name = (d.get("name") or "").strip()
    if not name:
        return _err("boat name is required")
    try:
        cur = db.execute(
            "INSERT INTO boats(race_id,name,pin_hash,created_at,owner_id) "
            "VALUES (?,?,'',?,?)",
            (race_id, name, int(time.time()), u["id"]))
    except Exception:
        return _err("a boat with that name already exists in this race", 409)
    db.commit()
    return jsonify({"boat_id": cur.lastrowid})


@app.get("/api/races/<int:race_id>/my_boats")
def my_boats(race_id):
    db = get_db()
    u = current_user(db)
    if not u:
        return jsonify([])
    rows = db.execute(
        "SELECT id, name, sim_time, finished_at FROM boats "
        "WHERE race_id=? AND owner_id=? ORDER BY id", (race_id, u["id"])).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/boats/<int:boat_id>/claim")
def claim_boat(boat_id):
    """One-time adoption of a pre-account boat using its old PIN."""
    db = get_db()
    u = current_user(db)
    if not u:
        return _err("sign in first", 401)
    b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    if not b:
        return _err("boat not found", 404)
    if b["owner_id"]:
        return _err("that boat already has an owner", 409)
    d = request.get_json(force=True)
    if not b["pin_hash"] or _hash_pin(str(d.get("pin") or "")) != b["pin_hash"]:
        return _err("wrong PIN", 403)
    db.execute("UPDATE boats SET owner_id=?, pin_hash='' WHERE id=?", (u["id"], boat_id))
    db.commit()
    return jsonify({"ok": True, "boat_id": boat_id, "name": b["name"]})


def _auth_boat(db, boat_id):
    """The signed-in owner (or an admin) controls the boat."""
    b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    if not b:
        return None, _err("boat not found", 404)
    u = current_user(db)
    if not u:
        return None, _err("sign in to manage this boat", 401)
    if b["owner_id"] != u["id"] and not u["is_admin"]:
        return None, _err("this boat belongs to another navigator", 403)
    return b, None


@app.post("/api/boats/<int:boat_id>/route")
def submit_route(boat_id):
    """Submit or update a routing.

    The engine first advances the boat to the current time under its previous
    routing — everything already sailed is locked — then the not-yet-reached
    waypoints are replaced.  Time never rewinds and past waypoints cannot be
    edited, so a routing can only be improved with information available while
    'on board'.
    """
    db = get_db()
    d = request.get_json(force=True)
    b, err = _auth_boat(db, boat_id)
    if err:
        return err
    race = _race_or_404(db, b["race_id"])
    now = int(time.time())

    wx = wind_health(db, now)
    if wx["degraded"]:
        return _err(
            "routing uploads are paused: the weather source is unreachable and "
            f"{wx['synthetic_cells']} wind cell(s) hold placeholder data — "
            "submitting against fake weather wouldn't be fair. The server "
            "retries every 15 minutes; see the committee log for recovery.", 503)

    if "gpx" in d or "csv" in d:
        try:
            wps = parse_route(d.get("gpx") or d.get("csv"))
        except Exception as e:
            return _err(f"could not parse route file: {e}")
    else:
        wps = [(float(p[0]), float(p[1])) for p in d.get("waypoints", [])]
    if not wps:
        return _err("no waypoints found in submission")
    if len(wps) > 10000:
        return _err("too many waypoints (max 10,000)")

    marks = get_marks(db, race["id"])

    if b["sim_time"] is None:
        # first routing: boat starts at the start line, never earlier than now
        start_at = max(race["start_time"], now)
        db.execute("UPDATE boats SET sim_time=?, lat=?, lon=?, next_mark=1 WHERE id=?",
                   (start_at, marks[0]["lat"], marks[0]["lon"], b["id"]))
        db.commit()
        b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    else:
        catch_up_race(db, race["id"], now)   # lock the past before editing
        b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
        if b["finished_at"]:
            return _err("boat has finished — routing is closed", 409)

    # softly reconcile the routing with the course: join a re-uploaded
    # routing at the boat (course-over-ground aware, so out-and-back legs
    # can't fool it), skip waypoints already underfoot, and make sure every
    # remaining mark (finish included) is actually visited — inserted, not
    # disqualified, when the routing's own mark placement differs
    last2 = db.execute("SELECT lat,lon FROM track WHERE boat_id=? "
                       "ORDER BY t DESC LIMIT 2", (boat_id,)).fetchall()
    cog = None
    if len(last2) == 2:
        from vn.geo import bearing_deg, haversine_nm
        if haversine_nm(last2[1]["lat"], last2[1]["lon"],
                        last2[0]["lat"], last2[0]["lon"]) > 0.05:
            cog = bearing_deg(last2[1]["lat"], last2[1]["lon"],
                              last2[0]["lat"], last2[0]["lon"])
    wps, adjustments = enforce_course(
        wps, marks, b["next_mark"], race["mark_radius_nm"],
        (b["lat"], b["lon"]), cog)

    for i, (lat, lon) in enumerate(wps):
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return _err(f"waypoint {i} out of range")
    row = db.execute("SELECT COALESCE(MAX(seq),-1) m FROM route_wps WHERE boat_id=? AND passed=1",
                     (boat_id,)).fetchone()
    base = row["m"] + 1
    db.execute("DELETE FROM route_wps WHERE boat_id=? AND passed=0", (boat_id,))
    db.executemany("INSERT INTO route_wps(boat_id,seq,lat,lon) VALUES (?,?,?,?)",
                   [(boat_id, base + i, lat, lon) for i, (lat, lon) in enumerate(wps)])
    db.execute("INSERT INTO route_log(boat_id,submitted_at,wp_json) VALUES (?,?,?)",
               (boat_id, now, json.dumps(wps)))
    db.commit()
    return jsonify({"ok": True, "waypoints": len(wps),
                    "adjustments": adjustments,
                    "locked_until": b["sim_time"] if b["sim_time"] else None})


@app.get("/api/boats/<int:boat_id>")
def boat_detail(boat_id):
    """Owner view (signed in): includes the private future routing."""
    db = get_db()
    b, err = _auth_boat(db, boat_id)
    if err:
        return err
    race = _race_or_404(db, b["race_id"])
    catch_up_race(db, race["id"])
    b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    marks = get_marks(db, race["id"])
    trk = db.execute("SELECT t,lat,lon,twd,tws,bsp,hdg FROM track WHERE boat_id=? ORDER BY t",
                     (boat_id,)).fetchall()
    wps = db.execute("SELECT seq,lat,lon FROM route_wps WHERE boat_id=? AND passed=0 ORDER BY seq",
                     (boat_id,)).fetchall()
    log = db.execute("SELECT submitted_at, wp_json FROM route_log WHERE boat_id=? "
                     "ORDER BY submitted_at DESC LIMIT 20", (boat_id,)).fetchall()
    return jsonify({
        "id": b["id"], "name": b["name"], "race_id": b["race_id"],
        "sim_time": b["sim_time"], "lat": b["lat"], "lon": b["lon"],
        "finished_at": b["finished_at"], "maneuvers": b["maneuvers"] or 0,
        "groundings": b["groundings"] or 0,
        "zone_steps": b["zone_steps"] or 0,
        "dtf": dtf_nm(b["lat"], b["lon"], marks, b["next_mark"]) if b["lat"] is not None else None,
        "route": [[w["lat"], w["lon"]] for w in wps],
        "track": [dict(p) for p in _decimate(trk)],
        "submissions": [{"at": l["submitted_at"],
                         "n": len(json.loads(l["wp_json"]))} for l in log],
    })


def _fmt_dm(v, is_lat):
    hemi = ("N" if v >= 0 else "S") if is_lat else ("E" if v >= 0 else "W")
    v = abs(v)
    deg = int(v)
    return f"{deg:0{2 if is_lat else 3}d}° {(v - deg) * 60:05.2f}' {hemi}"


def _boat_fix(db, b):
    """Latest simulated fix for a boat, or None before its start."""
    last = db.execute(
        "SELECT * FROM track WHERE boat_id=? ORDER BY t DESC LIMIT 1",
        (b["id"],)).fetchone()
    if not last:
        return None
    return {"t": last["t"],
            "time_utc": dt.datetime.fromtimestamp(last["t"], dt.timezone.utc)
                          .strftime("%Y-%m-%d %H:%M UTC"),
            "lat": last["lat"], "lon": last["lon"],
            "position": f'{_fmt_dm(last["lat"], True)}  {_fmt_dm(last["lon"], False)}',
            "sog_kn": last["bsp"], "hdg": last["hdg"],
            "twd": last["twd"], "tws_kn": last["tws"]}


@app.get("/api/boats/<int:boat_id>/position")
def boat_position(boat_id):
    """The boat's latest fix — positions are public, like any race tracker."""
    db = get_db()
    b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    if not b:
        return _err("boat not found", 404)
    catch_up_race(db, b["race_id"])
    fix = _boat_fix(db, b)
    if not fix:
        return _err("boat has not started yet", 404)
    marks = get_marks(db, b["race_id"])
    b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    fix.update({"boat": b["name"], "finished": b["finished_at"] is not None,
                "dtf_nm": round(dtf_nm(b["lat"], b["lon"], marks, b["next_mark"]), 1)})
    return jsonify(fix)


@app.get("/api/boats/<int:boat_id>/position.gpx")
def boat_position_gpx(boat_id):
    """Current position as a one-waypoint GPX — import it into your routing
    software as the departure point for the next routing run."""
    db = get_db()
    b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    if not b:
        return _err("boat not found", 404)
    catch_up_race(db, b["race_id"])
    fix = _boat_fix(db, b)
    if not fix:
        return _err("boat has not started yet", 404)
    iso = dt.datetime.fromtimestamp(fix["t"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = b["name"].replace("&", "&amp;").replace("<", "&lt;")
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<gpx version="1.1" creator="Virtual Navigator" '
           'xmlns="http://www.topografix.com/GPX/1/1">\n'
           f'  <wpt lat="{fix["lat"]:.6f}" lon="{fix["lon"]:.6f}">'
           f'<time>{iso}</time><name>{name} — current position</name></wpt>\n'
           '</gpx>\n')
    return Response(gpx, mimetype="application/gpx+xml",
                    headers={"Content-Disposition":
                             f'attachment; filename="{b["name"]}_position.gpx"'})


@app.get("/api/boats/<int:boat_id>/track.gpx")
def boat_track_gpx(boat_id):
    db = get_db()
    b = db.execute("SELECT * FROM boats WHERE id=?", (boat_id,)).fetchone()
    if not b:
        return _err("boat not found", 404)
    trk = db.execute("SELECT t,lat,lon FROM track WHERE boat_id=? ORDER BY t",
                     (boat_id,)).fetchall()
    gpx = track_to_gpx(b["name"], [(p["t"], p["lat"], p["lon"]) for p in trk])
    return Response(gpx, mimetype="application/gpx+xml",
                    headers={"Content-Disposition":
                             f'attachment; filename="{b["name"]}_track.gpx"'})


# ---------- real (tracked) boats -------------------------------------------

def _auth_admin(db, race_id):
    """Race management: signed-in admin accounts only."""
    r = _race_or_404(db, race_id)
    if not r:
        return None, _err("race not found", 404)
    u = current_user(db)
    if u and u["is_admin"]:
        return r, None
    return None, _err("admin access required — sign in as an admin", 403)


@app.post("/api/races/<int:race_id>/real_boats")
def add_real_boat(race_id):
    db = get_db()
    d = request.get_json(force=True)
    r, err = _auth_admin(db, race_id)
    if err:
        return err
    name = (d.get("name") or "").strip()
    if not name:
        return _err("name required")
    try:
        cur = db.execute("INSERT INTO real_boats(race_id,name,klass) VALUES (?,?,?)",
                         (race_id, name, d.get("klass", "")))
    except Exception:
        return _err("real boat already exists", 409)
    db.commit()
    return jsonify({"real_boat_id": cur.lastrowid})


@app.post("/api/real_boats/<int:rb_id>/track")
def import_real_track(rb_id):
    """Import tracker positions (GPX track or CSV with time,lat,lon columns)."""
    db = get_db()
    d = request.get_json(force=True)
    rb = db.execute("SELECT * FROM real_boats WHERE id=?", (rb_id,)).fetchone()
    if not rb:
        return _err("real boat not found", 404)
    race, err = _auth_admin(db, rb["race_id"])
    if err:
        return err
    try:
        pts = parse_track(d.get("text", ""))
    except Exception as e:
        return _err(f"could not parse track: {e}")
    if not pts:
        return _err("no timestamped positions found")
    marks = get_marks(db, race["id"])
    added = ingest_points(db, race, marks, rb_id, pts)
    return jsonify({"ok": True, "positions": added})


# ---------- YB Tracking link -------------------------------------------------

@app.post("/api/races/<int:race_id>/yb")
def link_yb(race_id):
    """Link a YB Tracking race: import the fleet roster and full track history,
    then let the background poller keep positions fresh.

    Body: {slug, model_filter?, exclude?} — model_filter keeps only
    boats whose model string contains the given text (case-insensitive), e.g.
    to import just the class that sails your polar; exclude drops boats whose
    name contains any of the given comma-separated substrings (backup trackers,
    spares: "secondary,zz_").
    """
    db = get_db()
    d = request.get_json(force=True)
    race, err = _auth_admin(db, race_id)
    if err:
        return err
    slug = (d.get("slug") or "").strip().strip("/")
    if not slug:
        return _err("YB race slug required (the bit after yb.tl/)")
    try:
        setup = yb.race_setup(slug)
    except Exception as e:
        return _err(f"could not read yb.tl/{slug}: {e}", 502)
    flt = (d.get("model_filter") or "").strip().lower()
    excl = [s.strip().lower()
            for s in (d.get("exclude") or "").split(",") if s.strip()]
    teams = [t for t in setup["teams"]
             if (not flt or flt in t["model"].lower())
             and not any(x in t["name"].lower() for x in excl)]
    if not teams:
        return _err("no teams matched", 404)

    marks = get_marks(db, race_id)
    by_yb = {}
    for t in teams:
        name = t["name"][:60] or f"YB {t['yb_id']}"
        row = db.execute("SELECT id FROM real_boats WHERE race_id=? AND name=?",
                         (race_id, name)).fetchone()
        if row:
            db.execute("UPDATE real_boats SET yb_id=?, klass=? WHERE id=?",
                       (t["yb_id"], t["model"][:60], row["id"]))
            by_yb[t["yb_id"]] = row["id"]
        else:
            cur = db.execute(
                "INSERT INTO real_boats(race_id,name,klass,yb_id) VALUES (?,?,?,?)",
                (race_id, name, t["model"][:60], t["yb_id"]))
            by_yb[t["yb_id"]] = cur.lastrowid
    db.execute("UPDATE races SET yb_slug=? WHERE id=?", (slug, race_id))
    db.commit()

    imported = 0
    try:
        pos = yb.positions(slug)                 # full history
    except Exception as e:
        return jsonify({"ok": True, "race": setup["title"], "teams": len(teams),
                        "positions": 0, "note": f"roster linked; positions failed: {e}"})
    for yb_id, pts in pos.items():
        rb_id = by_yb.get(yb_id)
        if rb_id and pts:
            imported += ingest_points(db, race, marks, rb_id, pts)
    return jsonify({"ok": True, "race": setup["title"], "teams": len(teams),
                    "positions": imported})


@app.get("/api/races/<int:race_id>/log")
def race_committee_log(race_id):
    """The committee log: every course/zone/start/routing change, with when
    and why — full transparency for competitors."""
    db = get_db()
    if not _race_or_404(db, race_id):
        return _err("race not found", 404)
    rows = db.execute(
        "SELECT at, message FROM race_log WHERE race_id=? "
        "ORDER BY at DESC, id DESC LIMIT 200", (race_id,)).fetchall()
    return jsonify([{"at": r["at"], "message": r["message"]} for r in rows])


# ---------- real vs virtual: why the gap? -----------------------------------

_compare_memo = {}       # (rb_id, boat_id) -> (key, result)
COMPARE_MEMO_MAX = 64


@app.get("/api/races/<int:race_id>/compare")
def race_compare(race_id):
    """Split the gap between a tracked real boat and a virtual boat into
    boat speed (the real boat against the race polar on its own track),
    navigation (where each boat went) and start offset — see vn/compare.py.
    Without ?virtual= only the real boat's polar report is returned.
    Tracks are public, so the analysis is too."""
    db = get_db()
    r = _race_or_404(db, race_id)
    if not r:
        return _err("race not found", 404)
    try:
        rb_id = int(request.args.get("real", ""))
    except ValueError:
        return _err("real=<real boat id> is required")
    rb = db.execute("SELECT * FROM real_boats WHERE id=? AND race_id=?",
                    (rb_id, race_id)).fetchone()
    if not rb:
        return _err("real boat not found", 404)
    boat = None
    if request.args.get("virtual"):
        try:
            boat_id = int(request.args["virtual"])
        except ValueError:
            return _err("virtual=<boat id> must be a number")
        catch_up_race(db, race_id)
        boat = db.execute("SELECT * FROM boats WHERE id=? AND race_id=?",
                          (boat_id, race_id)).fetchone()
        if not boat:
            return _err("virtual boat not found", 404)
    # re-sailing a long track is real work; reuse the answer until either
    # track has advanced
    key = (rb["last_t"], boat["sim_time"] if boat else None,
           boat["finished_at"] if boat else None)
    memo_id = (rb_id, boat["id"] if boat else None)
    hit = _compare_memo.get(memo_id)
    if hit and hit[0] == key:
        return jsonify(hit[1])
    marks = get_marks(db, race_id)
    try:
        out = compare(db, r, marks, rb, boat)
    except CompareError as e:
        return _err(str(e), 409)
    if len(_compare_memo) >= COMPARE_MEMO_MAX:
        _compare_memo.pop(next(iter(_compare_memo)))
    _compare_memo[memo_id] = (key, out)
    return jsonify(out)


# ---------- on-board forecast snapshots -------------------------------------

@app.get("/api/races/<int:race_id>/forecasts")
def list_forecasts(race_id):
    db = get_db()
    if not _race_or_404(db, race_id):
        return _err("race not found", 404)
    rows = db.execute(
        "SELECT id, issued_at, meta_json FROM forecast_snapshots "
        "WHERE race_id=? ORDER BY issued_at DESC LIMIT 60", (race_id,)).fetchall()
    return jsonify([{"id": r["id"], "issued_at": r["issued_at"],
                     **{k: json.loads(r["meta_json"]).get(k)
                        for k in ("step", "ni", "nj", "bytes")},
                     "hours": len(json.loads(r["meta_json"]).get("hours", []))}
                    for r in rows])


@app.get("/api/forecasts/<int:snap_id>.grb")
def download_forecast(snap_id):
    db = get_db()
    row = db.execute("SELECT * FROM forecast_snapshots WHERE id=?",
                     (snap_id,)).fetchone()
    if not row:
        return _err("snapshot not found", 404)
    stamp = dt.datetime.fromtimestamp(row["issued_at"], dt.timezone.utc)
    fname = f"race{row['race_id']}_wind_{stamp.strftime('%Y%m%d_%H%M')}z.grb"
    return Response(row["grib"], mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------- background ticker -----------------------------------------------
# Advances every active race on a schedule (so boats sail even when nobody is
# watching), polls linked YB trackers, and captures forecast snapshots.

TICK_SECONDS = 60
YB_POLL_SECONDS = 600
SNAPSHOT_SECONDS = 6 * 3600
_yb_last = {}


_wx_degraded = None      # last observed weather health (None until first tick)


def _tick():
    global _wx_degraded
    db = get_db()
    now = int(time.time())
    live = []
    for r in db.execute("SELECT * FROM races").fetchall():
        if now < r["start_time"] - 72 * 3600 or now > r["start_time"] + 60 * 86400:
            continue                                   # far from race window
        live.append(r)
        catch_up_race(db, r["id"], now)

    # weather health transitions go on every live race's committee log
    wx = wind_health(db, now)
    if _wx_degraded is not None and wx["degraded"] != _wx_degraded:
        msg = (f"⚠ Weather source degraded: {wx['synthetic_cells']} wind "
               "cell(s) fell back to placeholder data (API unreachable). "
               "Routing uploads are paused; the server retries every 15 min."
               if wx["degraded"] else
               "Weather source restored — all wind cells carry real data "
               "again; routing uploads reopened.")
        for r in live:
            add_race_log(db, r["id"], msg)
        db.commit()
        print(f"[ticker] {msg}")
    _wx_degraded = wx["degraded"]

    for r in live:
        if r["yb_slug"] and now - _yb_last.get(r["id"], 0) >= YB_POLL_SECONDS:
            _yb_last[r["id"]] = now
            try:
                _poll_yb(db, r)
            except Exception:
                traceback.print_exc()

        latest = db.execute(
            "SELECT MAX(issued_at) m FROM forecast_snapshots WHERE race_id=?",
            (r["id"],)).fetchone()["m"]
        if latest is None or now - latest >= SNAPSHOT_SECONDS:
            try:
                make_snapshot(db, r)
                print(f"[ticker] forecast snapshot for race {r['id']}")
            except Exception:
                traceback.print_exc()


def _poll_yb(db, race):
    marks = get_marks(db, race["id"])
    pos = yb.positions(race["yb_slug"], latest_only=True)
    if not any(pos.values()):
        return
    boats = {rb["yb_id"]: rb["id"] for rb in db.execute(
        "SELECT id, yb_id FROM real_boats WHERE race_id=? AND yb_id IS NOT NULL",
        (race["id"],))}
    added = 0
    for yb_id, pts in pos.items():
        rb_id = boats.get(yb_id)
        if rb_id and pts:
            added += ingest_points(db, race, marks, rb_id, pts)
    if added:
        print(f"[ticker] yb.tl/{race['yb_slug']}: {added} new positions")


def _ticker_loop():
    while True:
        try:
            _tick()
        except Exception:
            traceback.print_exc()
        time.sleep(TICK_SECONDS)


_ticker_started = False


def start_ticker():
    global _ticker_started
    if _ticker_started:
        return
    _ticker_started = True
    threading.Thread(target=_ticker_loop, name="vn-ticker", daemon=True).start()


# under a production server (gunicorn etc.) there is no __main__ entry —
# opt into the ticker via env; run exactly one worker so it starts once
if os.environ.get("VN_ENABLE_TICKER") == "1":
    start_ticker()


if __name__ == "__main__":
    port = int(os.environ.get("VN_PORT") or os.environ.get("PORT") or 5170)
    start_ticker()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
