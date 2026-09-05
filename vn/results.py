"""Official results for the real fleet.

The tracker gives the leaderboard a finish when a boat's track reaches the
finish mark; the race committee's results are the truth: the finish time
to the second, elapsed and corrected time, and a status for every boat
that did not finish (RET, DNF, DNS, DSQ). Once imported they replace the
tracker's guess on the leaderboard and stay put when the track changes.

Sources:

  yachtscoring:<eventId>      Yacht Scoring's public API (the Vineyard Race
                              and most US offshore races). One event may
                              hold several races (race numbers); ours are
                              one-race events, so race 1 unless told.
  CSV text                    pasted or uploaded, with a header row naming
                              boat/yacht, sail, class, finish, elapsed,
                              corrected, status in any order.

Rows are matched to the race's roster by name (the AIS name rules: sponsor
suffixes ignored, case-insensitive) and by sail number. Nothing is written
until the caller applies a preview it has looked at.
"""
import csv
import datetime as dt
import io
import json
import re
import time
import urllib.request

from .ais import name_matches, normalize_name
from .db import add_race_log

YS_API = "https://api.yachtscoring.com/v1/public/event/{event}/result-detail-report?raceNumber={race}"
STATUSES = ("FIN", "RET", "DNF", "DNS", "DSQ", "DNC", "OCS", "RDG", "TLE")


def _http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "virtual-navigator/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def parse_source(source):
    """'yachtscoring:50775' or 'yachtscoring:50775#2' -> ('yachtscoring', '50775', 2)."""
    s = (source or "").strip()
    m = re.fullmatch(r"yachtscoring:(\d+)(?:#(\d+))?", s, re.I)
    if m:
        return "yachtscoring", m.group(1), int(m.group(2) or 1)
    m = re.search(r"yachtscoring\.com/(?:emenu|event_results_\w+)/(\d+)", s, re.I)
    if m:
        return "yachtscoring", m.group(1), 1
    m = re.search(r"yachtscoring\.com/.*[?&]eID=(\d+)", s, re.I)
    if m:
        return "yachtscoring", m.group(1), 1
    raise ValueError("Give a Yacht Scoring event as yachtscoring:<eventId> (or its emenu URL), "
                     "or paste CSV results.")


def _iso(s):
    if not s:
        return None
    d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def _status(code):
    c = (code or "").strip().upper()
    if c in ("AOK", "OK", "FIN", "FINISHED", ""):
        return "FIN"
    return c if c in STATUSES else c[:4] or "FIN"


def parse_yachtscoring(payload):
    """Rows from Yacht Scoring's result-detail-report: every boat in every
    circle, division and class of that race."""
    rows = []
    for circle in payload.get("data") or []:
        for div in circle.get("divisions") or []:
            for cls in div.get("classes") or []:
                for b in cls.get("boats") or []:
                    sail = " ".join(x for x in (b.get("sailPrefix"), b.get("sailNumber")) if x)
                    rows.append({
                        "name": (b.get("name") or "").strip(),
                        "sail_no": sail.strip() or None,
                        "klass": cls.get("className") or div.get("divisionName") or "",
                        "division": div.get("divisionName") or circle.get("circleName") or "",
                        "status": _status(b.get("finishStatus")),
                        "finish_at": _iso(b.get("finishTime")),
                        "elapsed_s": b.get("elapsedTime"),
                        "corrected_s": b.get("correctedTime"),
                        "place_class": b.get("placeClass"),
                        "place_overall": b.get("placeOverall"),
                    })
    return rows


def fetch_yachtscoring(event_id, race_number=1):
    return parse_yachtscoring(_http_json(YS_API.format(event=event_id, race=race_number)))


def _seconds(s):
    """'26:12:04', '1d 02:12:04', '94324' -> seconds; blank -> None."""
    s = (s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+", s):
        return int(s)
    days = 0
    m = re.match(r"(\d+)\s*d\s*(.*)", s, re.I)
    if m:
        days, s = int(m.group(1)), m.group(2)
    parts = [int(p) for p in s.split(":")] if re.fullmatch(r"\d+(:\d+){1,2}", s) else None
    if parts is None:
        return None
    while len(parts) < 3:
        parts.append(0)
    h, mi, se = parts
    return days * 86400 + h * 3600 + mi * 60 + se


def parse_csv(text):
    """Rows from a pasted results table. Headers are matched by keyword, so
    'Yacht', 'Boat Name', 'Sail #', 'Finish Time (UTC)', 'Elapsed', 'Corrected'
    and 'Status' all work. Finish times are ISO or 'YYYY-MM-DD HH:MM[:SS]', UTC."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]

    def col(*keys):
        for i, h in enumerate(head):
            if any(k in h for k in keys):
                return i
        return None
    i_name = col("boat", "yacht", "name")
    i_sail = col("sail")
    i_class = col("class", "division", "fleet")
    i_fin = col("finish")
    i_el = col("elapsed")
    i_cor = col("corrected")
    i_st = col("status", "result")
    i_pos = col("pos", "place", "rank")
    if i_name is None:
        raise ValueError("The CSV needs a column naming the boat (Boat, Yacht or Name).")
    out = []
    for r in rows[1:]:
        get = lambda i: r[i].strip() if i is not None and i < len(r) else ""
        finish = get(i_fin)
        finish_at = None
        if finish:
            try:
                finish_at = _iso(finish.replace(" ", "T", 1) if " " in finish and "T" not in finish else finish)
            except ValueError:
                finish_at = None
        status = _status(get(i_st)) if get(i_st) else ("FIN" if finish_at or get(i_el) else "DNS")
        out.append({"name": get(i_name), "sail_no": get(i_sail) or None, "klass": get(i_class),
                    "division": get(i_class), "status": status, "finish_at": finish_at,
                    "elapsed_s": _seconds(get(i_el)), "corrected_s": _seconds(get(i_cor)),
                    "place_class": int(get(i_pos)) if get(i_pos).isdigit() else None,
                    "place_overall": None})
    return out


def _sail_key(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def match_roster(db, race_id, rows):
    """Pair result rows with roster boats: by sail number first, then by name.
    Returns (matches [(rb_row, result)], unmatched_results, unmatched_roster)."""
    roster = db.execute("SELECT * FROM real_boats WHERE race_id=?", (race_id,)).fetchall()
    free = {rb["id"]: rb for rb in roster}
    matches, leftovers = [], []
    for res in rows:
        hit = None
        sk = _sail_key(res.get("sail_no"))
        if sk:
            for rb in free.values():
                if rb["sail_no"] and _sail_key(rb["sail_no"]) == sk:
                    hit = rb
                    break
        if hit is None:
            # suffixes can sit on either side: the committee may list
            # "Midnight Rider - PMP Strategy" for a roster "Midnight Rider"
            cands = [rb for rb in free.values()
                     if name_matches(res["name"], rb["name"]) or name_matches(rb["name"], res["name"])
                     or normalize_name(res["name"]) == normalize_name(rb["name"])]
            if len(cands) == 1:
                hit = cands[0]
        if hit is None:
            leftovers.append(res)
            continue
        matches.append((hit, res))
        del free[hit["id"]]
    return matches, leftovers, list(free.values())


def apply_results(db, race, matches, source, now=None):
    """Write the official result onto each matched boat and note it on the
    committee log. A finish time replaces the tracker's; a non-finish clears
    it. Returns a short summary."""
    now = int(now or time.time())
    fin = dnf = 0
    for rb, res in matches:
        finished_at = res["finish_at"] if res["status"] == "FIN" and res["finish_at"] else None
        if res["status"] == "FIN":
            fin += 1
        else:
            dnf += 1
        db.execute(
            "UPDATE real_boats SET official_status=?, official_finish=?, official_elapsed_s=?, "
            "official_corrected_s=?, official_place=?, official_place_overall=?, "
            "official_class=?, finished_at=COALESCE(?, CASE WHEN ?='FIN' THEN finished_at END), "
            "sail_no=COALESCE(sail_no, ?) WHERE id=?",
            (res["status"], res["finish_at"], res["elapsed_s"], res["corrected_s"],
             res["place_class"], res["place_overall"], res["klass"],
             finished_at, res["status"], res["sail_no"], rb["id"]))
    db.execute("UPDATE races SET results_source=?, results_at=? WHERE id=?",
               (source, now, race["id"]))
    add_race_log(db, race["id"],
                 f"Official results imported from {source}: {fin} finisher"
                 f"{'' if fin == 1 else 's'} with committee times, {dnf} "
                 f"{'boat' if dnf == 1 else 'boats'} scored RET, DNF or DNS. The leaderboard "
                 "now shows the committee's finish times for the real fleet.")
    db.commit()
    return {"finishers": fin, "non_finishers": dnf, "matched": len(matches)}


def preview_json(matches, unmatched_results, unmatched_roster):
    def res_json(r):
        return {k: r.get(k) for k in ("name", "sail_no", "klass", "status", "finish_at",
                                      "elapsed_s", "corrected_s", "place_class", "place_overall")}
    return {
        "matched": [{"boat": rb["name"], "boat_id": rb["id"], "result": res_json(res)}
                    for rb, res in matches],
        "unmatched_results": [res_json(r) for r in unmatched_results],
        "unmatched_roster": [{"boat": rb["name"], "boat_id": rb["id"]} for rb in unmatched_roster],
    }
