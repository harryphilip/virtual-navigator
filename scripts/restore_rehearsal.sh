#!/usr/bin/env bash
# Restore a Fly volume from a snapshot, the way it would be done for real,
# rehearsed on staging so the steps are known before they are needed.
#
#   bash scripts/restore_rehearsal.sh                      # staging, newest snapshot
#   bash scripts/restore_rehearsal.sh <snapshot id>        # staging, that snapshot
#   VN_APP=virtual-navigator bash scripts/restore_rehearsal.sh <snapshot id>   # production, for real
#
# What it does, one Machine at a time (both apps run exactly one):
#   1. reads the app's volume and Machine
#   2. takes a fresh snapshot when none is named (so the rehearsal restores
#      the state of a minute ago and the result is checkable)
#   3. creates a new volume from the snapshot, same name (the [mounts] name
#      in fly.toml), same region
#   4. clones the Machine with the new volume mounted at /data, waits for it
#      to pass /healthz
#   5. destroys the old Machine, then the old, now-detached volume
#   6. prints /api/races before and after so you can see the data came back
#
# Safe to stop between steps: nothing is destroyed until the clone is
# healthy. Needs flyctl signed in with rights on the app.
set -euo pipefail

APP="${VN_APP:-virtual-navigator-staging}"
SNAP="${1:-}"
case "$APP" in
  virtual-navigator) HOST=https://virtual-navigator.com ;;
  *) HOST="https://$APP.fly.dev" ;;
esac
J='python3 -c'

echo "== $APP"
vol=$(fly volumes list --app "$APP" --json | $J 'import json,sys; v=[x for x in json.load(sys.stdin) if x.get("attached_machine_id")]; print(v[0]["id"])')
name=$(fly volumes list --app "$APP" --json | $J "import json,sys; print([x for x in json.load(sys.stdin) if x['id']=='$vol'][0]['name'])")
region=$(fly volumes list --app "$APP" --json | $J "import json,sys; print([x for x in json.load(sys.stdin) if x['id']=='$vol'][0]['region'])")
size=$(fly volumes list --app "$APP" --json | $J "import json,sys; print([x for x in json.load(sys.stdin) if x['id']=='$vol'][0]['size_gb'])")
machine=$(fly machine list --app "$APP" --json | $J 'import json,sys; m=json.load(sys.stdin); print(m[0]["id"])')
echo "volume $vol ($name, ${size} GB, $region) on machine $machine"

echo "== races before"
before=$(curl -s -m 60 "$HOST/api/races" | $J 'import json,sys; print(sorted(r["name"] for r in json.load(sys.stdin)))')
echo "$before"

if [ -z "$SNAP" ]; then
  echo "== fresh snapshot"
  fly volumes snapshots create "$vol" --app "$APP"
  for i in $(seq 1 30); do
    SNAP=$(fly volumes snapshots list "$vol" --app "$APP" --json | $J 'import json,sys; s=[x for x in json.load(sys.stdin) if x.get("status")=="created"]; s.sort(key=lambda x: x.get("created_at","")); print(s[-1]["id"] if s else "")')
    [ -n "$SNAP" ] && break
    sleep 10
  done
fi
[ -n "$SNAP" ] || { echo "no usable snapshot"; exit 1; }
echo "restoring from snapshot $SNAP"

echo "== new volume from the snapshot"
newvol=$(fly volumes create "$name" --app "$APP" --region "$region" --size "$size" --snapshot-id "$SNAP" --yes --json | $J 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "new volume $newvol"

echo "== clone the machine onto it"
fly machine clone "$machine" --app "$APP" --region "$region" --attach-volume "$newvol:/data"
newmachine=$(fly machine list --app "$APP" --json | $J "import json,sys; print([m['id'] for m in json.load(sys.stdin) if m['id']!='$machine'][0])")
echo "new machine $newmachine; waiting for it to be healthy"
for i in $(seq 1 30); do
  ok=$(fly machine status "$newmachine" --app "$APP" --json 2>/dev/null | $J 'import json,sys; m=json.load(sys.stdin); print(all(c.get("status")=="passing" for c in m.get("checks",[])) and m.get("state")=="started")' || echo False)
  [ "$ok" = "True" ] && break
  sleep 10
done
[ "$ok" = "True" ] || { echo "clone $newmachine never became healthy; old machine $machine and volume $vol are untouched"; exit 1; }

echo "== retire the old machine and volume"
fly machine destroy "$machine" --app "$APP" --force
fly volumes destroy "$vol" --app "$APP" --yes

echo "== races after"
after=$(curl -s -m 120 "$HOST/api/races" | $J 'import json,sys; print(sorted(r["name"] for r in json.load(sys.stdin)))')
echo "$after"
[ "$before" = "$after" ] && echo "restore rehearsal OK: same races before and after" || echo "DIFFERENT race lists before and after — look before trusting this"
