"""Nightly off-box backup of the SQLite database to an S3-compatible bucket.

Fly's volume snapshots live with the same provider as the volume; this
puts a copy somewhere else. Configuration is the set of variables
`fly storage create` (Tigris) writes as secrets, so nothing else is
needed:

    BUCKET_NAME, AWS_ENDPOINT_URL_S3, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    AWS_REGION (Tigris uses "auto")

Any S3-compatible store works with the same five variables. The copy is
taken with SQLite's online backup API (consistent while the engine
writes), gzipped, and PUT with a Signature V4 request written here so no
SDK is pinned. Keys look like vn/2026-09-07/vn-2026-09-07T03-00Z.sqlite.gz;
the bucket's own lifecycle rule handles retention. The ticker runs it once
a day after BACKUP_HOUR_UTC; /healthz shows the last result.
"""
import datetime as dt
import gzip
import hashlib
import hmac
import io
import logging
import os
import sqlite3
import tempfile
import urllib.parse
import urllib.request

log = logging.getLogger("vn.backup")

BACKUP_HOUR_UTC = 3
state = {"last_at": None, "last_ok": None, "last_error": None, "last_key": None, "last_bytes": None}
_last_day = None


def config():
    """The five settings, or None when any is missing."""
    keys = ("BUCKET_NAME", "AWS_ENDPOINT_URL_S3", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    vals = {k: os.environ.get(k, "").strip() for k in keys}
    if not all(vals.values()):
        return None
    vals["AWS_REGION"] = os.environ.get("AWS_REGION", "").strip() or "auto"
    return vals


def configured():
    return config() is not None


def snapshot_gz(db_path):
    """A consistent copy of the live database, gzipped, as bytes. SQLite's
    online backup API copies pages under the engine's writes; the copy goes
    to a temporary file because sqlite3 has no bytes target."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
        tmp = tf.name
    try:
        src = sqlite3.connect(db_path, timeout=30)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(data)
    return buf.getvalue()


def _sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_put(cfg, key, body, now=None, content_type="application/gzip"):
    """(url, headers) for an S3 PUT of `body` at `key`, signed with AWS
    Signature Version 4 (path-style: <endpoint>/<bucket>/<key>)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    endpoint = cfg["AWS_ENDPOINT_URL_S3"].rstrip("/")
    host = urllib.parse.urlparse(endpoint).netloc
    path = "/" + urllib.parse.quote(cfg["BUCKET_NAME"] + "/" + key, safe="/-_.~")
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date,
               "content-type": content_type}
    signed = ";".join(sorted(headers))
    canonical = "\n".join(["PUT", path, "",
                           "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
                           signed, payload_hash])
    scope = f"{date}/{cfg['AWS_REGION']}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(("AWS4" + cfg["AWS_SECRET_ACCESS_KEY"]).encode(), date)
    k = _sign(k, cfg["AWS_REGION"])
    k = _sign(k, "s3")
    k = _sign(k, "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (f"AWS4-HMAC-SHA256 Credential={cfg['AWS_ACCESS_KEY_ID']}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}")
    out = {k2: v for k2, v in headers.items() if k2 != "host"}
    out["Authorization"] = auth
    out["Content-Length"] = str(len(body))
    return endpoint + path, out


def upload(cfg, key, body, opener=urllib.request.urlopen):
    url, headers = sigv4_put(cfg, key, body)
    req = urllib.request.Request(url, data=body, method="PUT", headers=headers)
    with opener(req, timeout=120) as resp:
        return resp.status


def run(db_path, now=None, opener=urllib.request.urlopen):
    """Take and upload one backup. Records the outcome in `state`; returns
    the key on success, raises on failure (the caller logs)."""
    cfg = config()
    if cfg is None:
        raise RuntimeError("backup not configured: BUCKET_NAME, AWS_ENDPOINT_URL_S3, "
                           "AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
    now = now or dt.datetime.now(dt.timezone.utc)
    key = f"vn/{now:%Y-%m-%d}/vn-{now:%Y-%m-%dT%H-%M}Z.sqlite.gz"
    try:
        body = snapshot_gz(db_path)
        upload(cfg, key, body, opener)
    except Exception as e:
        state.update({"last_at": int(now.timestamp()), "last_ok": False, "last_error": str(e)[:200]})
        raise
    state.update({"last_at": int(now.timestamp()), "last_ok": True, "last_error": None,
                  "last_key": key, "last_bytes": len(body)})
    return key


def nightly(db_path, now=None):
    """Called by the ticker every minute: runs once per UTC day, after
    BACKUP_HOUR_UTC, when configured. Returns the key when it ran."""
    global _last_day
    if not configured():
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.hour < BACKUP_HOUR_UTC or _last_day == now.date():
        return None
    _last_day = now.date()
    try:
        key = run(db_path, now)
        log.info("backup uploaded: %s (%d bytes)", key, state["last_bytes"])
        return key
    except Exception:
        log.exception("backup failed")
        return None


def summary():
    return {"configured": configured(), **state}
