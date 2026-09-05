#!/usr/bin/env bash
# Smoke test a deployed Virtual Navigator: it answers, it is healthy, and it
# is running the commit we think it is.
#
#   scripts/smoke.sh https://virtual-navigator-staging.fly.dev <git sha>
#
# Retries for up to two minutes, because a sleeping staging machine takes a
# few seconds to wake and a fresh production machine a little longer to
# finish its first tick.
set -u
base="$1"; want="${2:-}"
deadline=$((SECONDS + 120))
while :; do
  body=$(curl -s -m 20 "$base/healthz" || true)
  ok=$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print("ok" if d.get("ok") else "unhealthy", d.get("version",""))
except Exception: print("noanswer", "")' 2>/dev/null)
  set -- $ok
  status="${1:-noanswer}"; version="${2:-}"
  if [ "$status" = ok ] && { [ -z "$want" ] || [ "$version" = "$want" ]; }; then
    break
  fi
  if [ $SECONDS -ge $deadline ]; then
    echo "smoke: $base is $status, version '$version' (wanted '$want'): $body" >&2
    exit 1
  fi
  sleep 5
done
for path in / /how /api/races /robots.txt; do
  code=$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$base$path")
  if [ "$code" != 200 ]; then echo "smoke: $base$path answered $code" >&2; exit 1; fi
done
echo "smoke: $base healthy, version $version, pages answer"
