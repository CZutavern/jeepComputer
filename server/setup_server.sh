#!/bin/bash
#
# One-time setup of the Jeep stem server (GPU accelerated).
# Run ON THE SERVER as your normal user:
#
#   mkdir -p ~/jeep-stems && scp pi@jeep.local:jeep_computer/server/{stem_splitter.py,jeep-stem-splitter.service,setup_server.sh} ~/jeep-stems/
#   bash ~/jeep-stems/setup_server.sh
#
# Safe to run again. It:
#   1. creates ~/jeep-stems/{inbox,done,processed,failed}
#   2. makes a Python venv with CUDA PyTorch + Demucs (~3 GB download)
#   3. installs and starts the splitter as a user service (kept running
#      after logout with linger; that step asks for sudo)
#   4. lets the Jeep's key read ~/jeep-stems/done, read-only (rrsync), and
#      nothing else

set -e
BASE="$HOME/jeep-stems"
PI_KEY='ssh-ed25519 <pi-public-key> jeep-stems-sync (read-only stems pull)'

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "Folders"
mkdir -p "$BASE"/{inbox,done,processed,failed}
cd "$BASE"

say "GPU"
if command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,driver_version --format=csv,noheader; then
    DEVICE=cuda
else
    echo "No NVIDIA driver found: Demucs will run on the CPU (slow). Install the driver"
    echo "(sudo ubuntu-drivers install) and run this again to use the 1660 Super."
    DEVICE=cpu
fi

say "Python environment (CUDA PyTorch + Demucs)"
command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "Run: sudo apt install python3-venv"; exit 1; }
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q torch torchaudio --index-url https://download.pytorch.org/whl/cu121
venv/bin/pip install -q demucs soundfile
venv/bin/python -c "import torch, demucs; print('torch', torch.__version__, '| CUDA:', torch.cuda.is_available())"

say "Splitter service"
mkdir -p ~/.config/systemd/user
sed "s/^Environment=DEMUCS_DEVICE=.*/Environment=DEMUCS_DEVICE=$DEVICE/" jeep-stem-splitter.service \
    > ~/.config/systemd/user/jeep-stem-splitter.service
systemctl --user daemon-reload
systemctl --user enable --now jeep-stem-splitter
# Keep the service running when logged out. Needs sudo: if it can't ask
# (run over a non-interactive SSH session), say what to run instead.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    sudo -n loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER" < /dev/tty 2>/dev/null \
        || echo "NOTE: run once on the server:  sudo loginctl enable-linger $USER"
fi
systemctl --user --no-pager status jeep-stem-splitter | head -3

say "Jeep read-only access to done/"
RRSYNC=$(command -v rrsync || true)
[ -z "$RRSYNC" ] && [ -f /usr/share/doc/rsync/scripts/rrsync ] && RRSYNC=/usr/share/doc/rsync/scripts/rrsync
[ -z "$RRSYNC" ] && { echo "rrsync not found: sudo apt install rsync"; exit 1; }
mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
if ! grep -qF "jeep-stems-sync" ~/.ssh/authorized_keys; then
    echo "command=\"$RRSYNC -ro $BASE/done\",restrict $PI_KEY" >> ~/.ssh/authorized_keys
    echo "Added the Jeep's key (read-only, $BASE/done only)"
else
    echo "Jeep key already present"
fi

say "Done"
echo "Server user for the Jeep's sync config: $USER"
echo "Test: drop a song in $BASE/inbox and watch: journalctl --user -fu jeep-stem-splitter"
