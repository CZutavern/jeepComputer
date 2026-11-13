#!/bin/bash
#
# Pull finished stems from the stem server into ~/Music/Stems
# (jeep-stems-sync.service: every 15 min, on arriving home, or "SYNC" on
# the Music page). The server splits songs with Demucs (server/stem_splitter.py);
# this only ever reads its done/ folder, over a key the server restricts to
# read-only rsync of that folder (see docs/stem-server.md).
#
# Config: ~/.config/jeep_computer/stems_sync.json
#   {"host": "192.168.0.10", "user": "me"}
# Status for the Music page: ~/.cache/jeep_computer/stems_sync.json

CONFIG="$HOME/.config/jeep_computer/stems_sync.json"
STATUS="$HOME/.cache/jeep_computer/stems_sync.json"
KEY="$HOME/.ssh/jeep_stems_ed25519"
DEST="$HOME/Music/Stems/"

mkdir -p "$DEST" "$(dirname "$STATUS")"

status() {   # ok(true/false) message new_count
    printf '{"time": %s, "ok": %s, "message": "%s", "new": %s}\n' \
        "$(date +%s)" "$1" "$2" "${3:-0}" > "$STATUS"
    echo "$2"
}

# Testing: STEMS_SYNC_TEST_SOURCE=/some/done/ pulls from a local folder instead
if [ -n "$STEMS_SYNC_TEST_SOURCE" ]; then
    before=$(find "$DEST" -mindepth 1 -maxdepth 1 -type d | wc -l)
    rsync -a "$STEMS_SYNC_TEST_SOURCE" "$DEST" &&
        new=$(( $(find "$DEST" -mindepth 1 -maxdepth 1 -type d | wc -l) - before )) &&
        status true "Synced: $new new song$([ "$new" = 1 ] || echo s) (test)" "$new"
    exit
fi

host=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('host', ''))" 2>/dev/null)
user=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('user', ''))" 2>/dev/null)
if [ -z "$host" ] || [ -z "$user" ]; then
    status false "Stem server not set up"
    exit 0
fi

before=$(find "$DEST" -mindepth 1 -maxdepth 1 -type d | wc -l)

# The server's forced command (rrsync -ro ~/jeep-stems/done) maps "./" to
# its done/ folder. Songs are complete folders there, so a plain pull is safe.
if timeout 900 rsync -a --partial --timeout=60 \
        -e "ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new" \
        "$user@$host:./" "$DEST" 2> "$STATUS.err"; then
    after=$(find "$DEST" -mindepth 1 -maxdepth 1 -type d | wc -l)
    new=$(( after - before ))
    status true "Synced: $new new song$([ "$new" = 1 ] || echo s)" "$new"
else
    if grep -q 'Permission denied' "$STATUS.err"; then
        status false "Stem server not set up yet: it doesn't accept the Pi's key (docs/stem-server.md)"
    elif grep -q -e 'Could not resolve' -e 'timed out' -e 'No route' -e 'Connection refused' "$STATUS.err"; then
        status false "Stem server offline ($host)"
    else
        err=$(tail -1 "$STATUS.err" | tr -d '"\\' | cut -c1-120)
        status false "Sync failed: ${err:-cannot reach $host}"
    fi
    exit 1
fi
