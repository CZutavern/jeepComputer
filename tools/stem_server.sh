#!/bin/bash
#
# Set up and test the stem server from the Pi.
#
# Needs the setup key authorized on the server first (you type the server
# password once; the Pi never stores it):
#     ssh-copy-id -i ~/.ssh/jeep_setup_ed25519.pub user@server.local
#
# Usage:  tools/stem_server.sh check | install | test | git | all
#   check    can we log in; GPU, Python, rrsync on the server
#   install  copy server/ files and run server/setup_server.sh there
#   test     end to end: song -> server inbox -> Demucs -> done/ -> Pi sync
#   git      bare repo on the server (~/backups/jeep_computer.git) as the
#            Pi's "origin", pushed by the daily backup over a git-only key;
#            plus a write-only key for the daily backup archive (~/backups/jeep)
#   all      all of the above
#
# Afterwards the setup key can go (remove its line from the server's
# ~/.ssh/authorized_keys); the read-only stems key and git-only key stay.

set -o pipefail
cd "$(dirname "$0")/.." || exit 1

CONFIG="$HOME/.config/jeep_computer/stems_sync.json"
HOST=$(python3 -c "import json; print(json.load(open('$CONFIG'))['host'])")
USER_=$(python3 -c "import json; print(json.load(open('$CONFIG'))['user'])")
SETUP_KEY="$HOME/.ssh/jeep_setup_ed25519"
GIT_KEY="$HOME/.ssh/jeep_git_ed25519"
SSH=(ssh -i "$SETUP_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=8)
SCP=(scp -q -i "$SETUP_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=8)
TARGET="$USER_@$HOST"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
remote() { "${SSH[@]}" "$TARGET" "$@"; }

check() {
    say "Server login ($TARGET)"
    if ! remote true 2>/dev/null; then
        echo "Can't log in with the setup key yet. On the Pi, run once:"
        echo "  ssh-copy-id -i $SETUP_KEY.pub $TARGET"
        return 1
    fi
    remote 'echo "logged in as $(whoami) on $(hostname), $(lsb_release -ds 2>/dev/null)"
            nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null || echo "no NVIDIA driver"
            python3 --version; command -v rrsync || ls /usr/share/doc/rsync/scripts/rrsync 2>/dev/null || echo "no rrsync"
            df -h ~ | tail -1'
}

install() {
    check || return 1
    say "Copying server files"
    remote 'mkdir -p ~/jeep-stems' &&
        "${SCP[@]}" server/stem_splitter.py server/jeep-stem-splitter.service server/setup_server.sh \
            "$TARGET:jeep-stems/" || return 1
    say "Running setup_server.sh on the server (several minutes: ~3 GB of PyTorch)"
    remote 'bash ~/jeep-stems/setup_server.sh'
}

test_pipeline() {
    check >/dev/null || { check; return 1; }
    local name="Jeep pipeline test $(date +%H%M%S)"
    local song="/tmp/$name.flac"
    say "Test song -> server inbox"
    # Four tones standing in for instruments (Demucs will split what it can)
    ffmpeg -loglevel error -y -f lavfi -i "sine=f=110:d=20" -f lavfi -i "sine=f=440:d=20" \
        -f lavfi -i "anoisesrc=d=20:c=pink:a=0.1" -filter_complex "amix=inputs=3" -ac 2 "$song" || return 1
    "${SCP[@]}" "$song" "$TARGET:jeep-stems/inbox/" && rm -f "$song" || return 1

    say "Waiting for the split (up to 10 min)"
    local waited=0
    until remote "test -d ~/jeep-stems/done/$(printf %q "$name")" 2>/dev/null; do
        if remote "ls ~/jeep-stems/failed/ 2>/dev/null | grep -qF $(printf %q "$name")"; then
            echo "Split FAILED; log:"; remote "tail -20 ~/jeep-stems/failed/$(printf %q "$name").flac.log"; return 1
        fi
        sleep 10; waited=$((waited + 10))
        [ $waited -ge 600 ] && { echo "Timed out"; return 1; }
    done
    echo "Split done after ~$waited s"

    say "Pi pulls it (read-only key)"
    tools/stems_sync.sh || return 1
    if ls "$HOME/Music/Stems/$name"/{drums,bass,vocals,other}.flac >/dev/null 2>&1; then
        echo "PASS: $name is in ~/Music/Stems"
    else
        echo "FAIL: stems didn't arrive"; return 1
    fi
    say "Cleaning up the test song"
    rm -rf "$HOME/Music/Stems/$name"
    remote "rm -rf ~/jeep-stems/done/$(printf %q "$name") ~/jeep-stems/processed/$(printf %q "$name").flac"
}

git_remote() {
    check >/dev/null || { check; return 1; }
    say "Bare repo on the server"
    remote 'mkdir -p ~/backups && { [ -d ~/backups/jeep_computer.git ] || git init -q --bare ~/backups/jeep_computer.git; } && echo ready'
    say "Git-only key for the backups"
    local line="command=\"git-shell -c \\\"\$SSH_ORIGINAL_COMMAND\\\"\",restrict $(cat "$GIT_KEY.pub")"
    remote "grep -qF 'jeep-git-backup' ~/.ssh/authorized_keys || echo $(printf %q "$line") >> ~/.ssh/authorized_keys"
    say "Write-only key for the daily backup archive (~/backups/jeep)"
    remote 'mkdir -p ~/backups/jeep'
    local rr; rr=$(remote 'command -v rrsync || echo /usr/share/doc/rsync/scripts/rrsync')
    local bline="command=\"$rr -wo ~/backups/jeep\",restrict $(cat "$HOME/.ssh/jeep_backup_ed25519.pub")"
    remote "grep -qF 'jeep-backup (write-only' ~/.ssh/authorized_keys || echo $(printf %q "$bline") >> ~/.ssh/authorized_keys"
    say "Pi: origin -> server, first push"
    git remote remove origin 2>/dev/null
    git remote add origin "$TARGET:backups/jeep_computer.git"
    git config core.sshCommand "ssh -i $GIT_KEY -o IdentitiesOnly=yes -o BatchMode=yes"
    git push -q -u origin HEAD && echo "pushed $(git rev-parse --short HEAD) to the server"
}

case "$1" in
    check) check ;;
    install) install ;;
    test) test_pipeline ;;
    git) git_remote ;;
    all) install && test_pipeline && git_remote ;;
    *) sed -n '2,20p' "$0"; exit 2 ;;
esac
