"""Take an off-box backup of the database right now, from the server console.

    .venv/bin/python scripts/backup_now.py

On Fly:  fly ssh console -C "python /app/scripts/backup_now.py"

Needs the bucket settings `fly storage create` writes as secrets
(BUCKET_NAME, AWS_ENDPOINT_URL_S3, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
AWS_REGION); see vn/backup.py. The ticker does this by itself once a day.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vn import backup
from vn.db import DB_PATH


def main():
    if not backup.configured():
        print("backup not configured: set BUCKET_NAME, AWS_ENDPOINT_URL_S3, AWS_ACCESS_KEY_ID, "
              "AWS_SECRET_ACCESS_KEY (fly storage create does this)")
        sys.exit(1)
    key = backup.run(DB_PATH)
    print(f"uploaded {key} ({backup.state['last_bytes']} bytes)")


if __name__ == "__main__":
    main()
