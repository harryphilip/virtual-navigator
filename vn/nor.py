"""Notice of Race / Sailing Instructions → virtual race definition.

Race documents go in, a structured race comes out.  Two extractors:

* **Claude** (preferred): the PDFs/text are handed to the Claude API as
  document blocks with a structured-output schema — it reads the documents
  the way a navigator would (start schedule, course appendix, mark tables,
  time zone) and returns marks in decimal degrees.  Used automatically when
  the server has Anthropic credentials (ANTHROPIC_API_KEY or an `ant auth`
  profile).
* **Heuristic** (fallback): pypdf text extraction plus pattern matching for
  coordinate tables and start dates.  Works offline; good SIs with a proper
  course appendix parse fine, prose-only documents will need manual marks.

Extraction never invents coordinates: marks come out only if the documents
state them.
"""
import datetime as dt
import io
import re

MAX_DOC_BYTES = 15 * 1024 * 1024


# --------------------------------------------------------------------------
# result shape (kept as plain dicts so the fallback shares it)
# --------------------------------------------------------------------------

def _empty():
    return {"name": None, "organizer": None, "start_time_utc": None,
            "marks": [], "course_description": None, "distance_nm": None,
            "classes": [], "warnings": [], "extractor": None}


def extract_race(docs):
    """docs: [(filename, mime, data_bytes)] → extraction dict."""
    try:
        out = _extract_claude(docs)
        out["extractor"] = "claude"
        return out
    except Exception as e:
        out = _extract_heuristic(docs)
        out["extractor"] = "heuristic"
        reason = str(e)
        if "api_key" in reason.lower() or "authentication" in reason.lower() \
                or "could not resolve" in reason.lower():
            reason = ("no Anthropic credentials on the server — set "
                      "ANTHROPIC_API_KEY to enable AI document reading")
        out["warnings"].insert(0, f"AI extraction unavailable ({reason}); "
                                  "used the built-in pattern parser instead")
        return out


# --------------------------------------------------------------------------
# Claude extractor
# --------------------------------------------------------------------------

def _extract_claude(docs):
    import base64
    import anthropic
    from pydantic import BaseModel

    class Mark(BaseModel):
        name: str
        lat: float
        lon: float

    class RaceExtract(BaseModel):
        name: str
        organizer: str | None
        start_time_utc: str | None       # ISO 8601, UTC
        marks: list[Mark]
        course_description: str | None
        distance_nm: float | None
        classes: list[str]
        warnings: list[str]

    content = []
    for fname, mime, data in docs:
        if len(data) > MAX_DOC_BYTES:
            raise ValueError(f"{fname} is too large")
        if mime == "application/pdf" or fname.lower().endswith(".pdf"):
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.standard_b64encode(data).decode()},
            })
        else:
            content.append({"type": "text",
                            "text": f"--- {fname} ---\n" + data.decode("utf-8", "replace")})
    content.append({"type": "text", "text": (
        "These are the official documents (Notice of Race and/or Sailing "
        "Instructions) for a sailing race. Extract the race definition for a "
        "virtual regatta platform.\n"
        "- marks: the racing course in sailing order, first entry the start "
        "line, last entry the finish. Use ONLY positions stated in the "
        "documents (course appendix, mark tables, start/finish line "
        "descriptions), converted to signed decimal degrees (S and W "
        "negative). Never invent or estimate a position; omit marks whose "
        "coordinates are not given. If a start or finish position is given "
        "as a line between two points, use the midpoint.\n"
        "- start_time_utc: the first warning/start signal of the main race "
        "converted to UTC using the time zone stated or implied by the venue.\n"
        "- classes: the classes/divisions invited to enter.\n"
        "- warnings: anything a race admin must fix by hand — missing "
        "coordinates, multiple courses to choose from, ambiguous start "
        "times, marks you had to skip.")})

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model="claude-opus-5",
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
        output_format=RaceExtract,
    )
    parsed = response.parsed_output
    out = _empty()
    out.update(parsed.model_dump())
    # defensive: drop marks with out-of-range coordinates
    out["marks"] = [m for m in out["marks"]
                    if -90 <= m["lat"] <= 90 and -180 <= m["lon"] <= 180]
    return out


# --------------------------------------------------------------------------
# heuristic fallback
# --------------------------------------------------------------------------

COORD_PAIR = re.compile(
    r"(\d{1,2})[°\s]\s*(\d{1,2}(?:[.,]\d+)?)\s*['′]?\s*([NS])\s*[,;/ ]\s*"
    r"(\d{1,3})[°\s]\s*(\d{1,2}(?:[.,]\d+)?)\s*['′]?\s*([EW])", re.I)
COORD_DEC = re.compile(
    r"(-?\d{1,2}\.\d{3,})\s*[,;/ ]\s*(-?\d{1,3}\.\d{3,})")
DATE_RE = re.compile(
    r"(\d{1,2})\s+(Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|"
    r"Sep\w*|Oct\w*|Nov\w*|Dec\w*)\s+(\d{4})|"
    r"(Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|"
    r"Nov\w*|Dec\w*)\s+(\d{1,2}),?\s+(\d{4})", re.I)
# "14:55", "14.55", optionally with a zone/marker — or bare "1455" only when
# followed by an explicit marker, so years like 2025 never parse as times
TIME_RE = re.compile(
    r"\b([01]\d|2[0-3])[:.]([0-5]\d)\s*(hrs|hours|UTC|GMT|BST|CEST|CET|EDT|EST|local)?\b|"
    r"\b([01]\d|2[0-3])([0-5]\d)\s*(hrs|hours|UTC|GMT|BST|CEST|CET|EDT|EST|local)\b", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
TZ_OFFSET = {"utc": 0, "gmt": 0, "bst": 1, "cet": 1, "cest": 2,
             "est": -5, "edt": -4}


def _pdf_text(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_heuristic(docs):
    out = _empty()
    text = ""
    for fname, mime, data in docs:
        if mime == "application/pdf" or fname.lower().endswith(".pdf"):
            try:
                text += "\n" + _pdf_text(data)
            except Exception:
                out["warnings"].append(f"could not read PDF text from {fname}")
        else:
            text += "\n" + data.decode("utf-8", "replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # race name: first line mentioning race-ish words, else first line
    for l in lines[:40]:
        if re.search(r"\b(race|regatta|challenge|trophy|cup)\b", l, re.I) \
                and len(l) < 90:
            out["name"] = l
            break
    if not out["name"] and lines:
        out["name"] = lines[0][:90]
    if out["name"]:
        out["name"] = re.sub(
            r"\s*[—–:-]?\s*(sailing instructions?|notice of race)\s*$", "",
            out["name"], flags=re.I).strip() or out["name"]

    # marks: lines containing a coordinate pair; leading text is the name
    for l in lines:
        m = COORD_PAIR.search(l)
        if m:
            lat = int(m.group(1)) + float(m.group(2).replace(",", ".")) / 60
            lon = int(m.group(4)) + float(m.group(5).replace(",", ".")) / 60
            if m.group(3).upper() == "S":
                lat = -lat
            if m.group(6).upper() == "W":
                lon = -lon
        else:
            m = COORD_DEC.search(l)
            if not m:
                continue
            lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        name = l[:m.start()].strip(" .:-–—\t") or f"Mark {len(out['marks'])}"
        out["marks"].append({"name": name[:60], "lat": round(lat, 5),
                             "lon": round(lon, 5)})

    # start time: first date near the word "start"/"warning signal"
    start_dt = None
    for i, l in enumerate(lines):
        if re.search(r"\b(first start|warning signal|start(ing)? (time|signal)|"
                     r"race starts?)\b", l, re.I):
            window = " ".join(lines[max(0, i - 2):i + 3])
            d = DATE_RE.search(window)
            t = TIME_RE.search(window)
            if d:
                if d.group(1):
                    day, mon, year = int(d.group(1)), d.group(2), int(d.group(3))
                else:
                    mon, day, year = d.group(4), int(d.group(5)), int(d.group(6))
                hh, mm, off = 12, 0, 0
                if t:
                    hh = int(t.group(1) or t.group(4))
                    mm = int(t.group(2) or t.group(5))
                    off = TZ_OFFSET.get((t.group(3) or t.group(6) or "utc").lower(), 0)
                try:
                    start_dt = dt.datetime(year, MONTHS[mon[:3].lower()], day,
                                           hh, mm, tzinfo=dt.timezone.utc) \
                               - dt.timedelta(hours=off)
                except (ValueError, KeyError):
                    pass
                if t is None:
                    out["warnings"].append(
                        "start date found but no time — assumed 12:00 UTC")
                break
    if start_dt:
        out["start_time_utc"] = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        out["warnings"].append("no start time found in the documents")
    if len(out["marks"]) < 2:
        out["warnings"].append(
            "fewer than two marks with coordinates found — add the course "
            "by hand (the pattern parser only reads explicit lat/lon tables)")
    return out
