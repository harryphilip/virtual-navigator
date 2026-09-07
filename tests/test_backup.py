"""Off-box backups: the SQLite copy is consistent and gzipped, the PUT is
signed the way S3 expects, the nightly runs once a day, and /healthz
shows the outcome."""
import datetime as dt
import gzip
import io
import sqlite3

import pytest

from vn import backup

CFG = {"BUCKET_NAME": "vn-backups", "AWS_ENDPOINT_URL_S3": "https://fly.storage.tigris.dev",
       "AWS_ACCESS_KEY_ID": "tid_example", "AWS_SECRET_ACCESS_KEY": "tsec_example",
       "AWS_REGION": "auto"}


@pytest.fixture
def configured(monkeypatch):
    for k, v in CFG.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(backup, "_last_day", None)
    backup.state.update({"last_at": None, "last_ok": None, "last_error": None,
                         "last_key": None, "last_bytes": None})


class FakeResp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_snapshot_is_a_gzipped_consistent_copy(db, tmp_path):
    from vn.db import DB_PATH
    db.execute("INSERT INTO users(username,display_name,salt,pass_hash,created_at) VALUES "
               "('nav','Nav','s','h',1)")
    db.commit()
    blob = backup.snapshot_gz(DB_PATH)
    raw = gzip.decompress(blob)
    assert raw[:16] == b"SQLite format 3\x00"
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(raw)
    c = sqlite3.connect(copy)
    assert c.execute("SELECT username FROM users").fetchone()[0] == "nav"


def test_sigv4_put_matches_the_aws_recipe():
    now = dt.datetime(2026, 9, 7, 3, 0, tzinfo=dt.timezone.utc)
    url, h = backup.sigv4_put(CFG, "vn/2026-09-07/x.sqlite.gz", b"hello", now=now)
    assert url == "https://fly.storage.tigris.dev/vn-backups/vn/2026-09-07/x.sqlite.gz"
    assert h["x-amz-date"] == "20260907T030000Z"
    assert h["x-amz-content-sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert h["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=tid_example/20260907/auto/s3/aws4_request, "
        "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, Signature=")
    sig = h["Authorization"].rsplit("Signature=", 1)[1]
    assert len(sig) == 64 and int(sig, 16)
    # deterministic: same inputs, same signature
    assert backup.sigv4_put(CFG, "vn/2026-09-07/x.sqlite.gz", b"hello", now=now)[1]["Authorization"] == h["Authorization"]


def test_run_uploads_and_records(db, configured):
    from vn.db import DB_PATH
    seen = {}
    def opener(req, timeout):
        seen["url"] = req.full_url; seen["method"] = req.get_method(); seen["len"] = len(req.data)
        return FakeResp()
    now = dt.datetime(2026, 9, 7, 3, 5, tzinfo=dt.timezone.utc)
    key = backup.run(DB_PATH, now=now, opener=opener)
    assert key == "vn/2026-09-07/vn-2026-09-07T03-05Z.sqlite.gz"
    assert seen["method"] == "PUT" and seen["url"].endswith(key) and seen["len"] > 100
    s = backup.summary()
    assert s["configured"] and s["last_ok"] and s["last_key"] == key and s["last_bytes"] == seen["len"]


def test_run_records_a_failure(db, configured):
    from vn.db import DB_PATH
    def opener(req, timeout):
        raise OSError("connection refused")
    with pytest.raises(OSError):
        backup.run(DB_PATH, opener=opener)
    assert backup.state["last_ok"] is False and "refused" in backup.state["last_error"]


def test_nightly_runs_once_a_day_after_the_hour(db, configured, monkeypatch):
    from vn.db import DB_PATH
    calls = []
    monkeypatch.setattr(backup, "run", lambda path, now: calls.append(now) or "k")
    day = dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc)
    assert backup.nightly(DB_PATH, day.replace(hour=2)) is None          # too early
    assert backup.nightly(DB_PATH, day.replace(hour=3, minute=1)) == "k"
    assert backup.nightly(DB_PATH, day.replace(hour=9)) is None          # already done today
    assert backup.nightly(DB_PATH, (day + dt.timedelta(days=1)).replace(hour=3)) == "k"
    assert len(calls) == 2


def test_unconfigured_is_quiet(db, monkeypatch):
    from vn.db import DB_PATH
    monkeypatch.delenv("BUCKET_NAME", raising=False)
    assert not backup.configured()
    assert backup.nightly(DB_PATH) is None
    with pytest.raises(RuntimeError):
        backup.run(DB_PATH)


def test_healthz_shows_the_backup_state(client, configured):
    body = client.get("/healthz").get_json()
    assert body["backup"]["configured"] is True and body["backup"]["last_ok"] is None
