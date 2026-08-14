#!/bin/bash
# Symlink install: nothing is copied outside ~/.config/hushkeys, which holds the
# personal configuration. A `git pull` is therefore enough to update.

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/hushkeys-venv"
CONFIG_DIR="$HOME/.config/hushkeys"
SYSTEMD_USER="$HOME/.config/systemd/user"

# Paths used before the project was renamed to hushkeys. Kept only so that an
# existing install can be migrated and its leftovers pointed out.
OLD_CONFIG_DIR="$HOME/.config/dictation"
OLD_VENV="$HOME/.local/share/dictation-venv"

green() { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '\033[33m!\033[0m %s\n' "$1"; }
step()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ─── 1. System dependencies ──────────────────────────────────────────────────
step "1/6 — System dependencies"

MISSING=()
for cmd in parecord socat notify-send ydotool ydotoold wl-copy wl-paste; do
    command -v "$cmd" >/dev/null 2>&1 || MISSING+=("$cmd")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    warn "Missing: ${MISSING[*]}"
    echo "    sudo apt install -y pulseaudio-utils socat libnotify-bin ydotool wl-clipboard"
    exit 1
fi
green "parecord, socat, notify-send, ydotool, wl-clipboard present"

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    warn "uv missing — needed to install a dedicated CPython."
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "    See README, section “A dedicated CPython 3.12, not the system Python”."
    exit 1
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
green "uv present"

# ─── 2. Python environment ───────────────────────────────────────────────────
step "2/6 — Python environment"

if [ -x "$VENV/bin/python" ]; then
    green "venv already present ($VENV)"
else
    # Python 3.12 rather than the system Python: ctranslate2 does not publish
    # wheels for 3.14 yet, which is what Ubuntu 26.04 ships.
    "$UV" python install 3.12
    "$UV" venv --python 3.12 "$VENV"
    VIRTUAL_ENV="$VENV" "$UV" pip install faster-whisper
    green "faster-whisper installed"
fi

if [ -d "$VENV"/lib/python*/site-packages/nvidia ]; then
    green "CUDA libraries present"
elif command -v nvidia-smi >/dev/null 2>&1; then
    VIRTUAL_ENV="$VENV" "$UV" pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12
    green "CUDA libraries installed"
else
    warn "No NVIDIA GPU detected — will run on CPU int8."
fi

# ─── 3. Configuration ────────────────────────────────────────────────────────
step "3/6 — Configuration"

mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/vocabulary.txt" ]; then
    green "existing vocabulary kept"
elif [ -f "$OLD_CONFIG_DIR/vocabulary.txt" ]; then
    # Copied, not moved: the old directory is left intact so nothing is lost if
    # the migration turns out badly.
    cp "$OLD_CONFIG_DIR/vocabulary.txt" "$CONFIG_DIR/vocabulary.txt"
    green "vocabulary carried over from $OLD_CONFIG_DIR"
else
    cp "$REPO/config/vocabulary.txt.example" "$CONFIG_DIR/vocabulary.txt"
    green "vocabulary initialised from the template"
fi

# ─── 4. Symlinks ─────────────────────────────────────────────────────────────
step "4/6 — Symlinks"

mkdir -p "$HOME/bin" "$SYSTEMD_USER"
ln -sfn "$REPO/bin/hushkeys" "$HOME/bin/hushkeys"
green "~/bin/hushkeys"
# The unit points at ~/bin/hushkeys-daemon rather than into the repository:
# that is what makes it independent of where the repository is cloned.
ln -sfn "$REPO/bin/hushkeys-daemon" "$HOME/bin/hushkeys-daemon"
green "~/bin/hushkeys-daemon"
ln -sfn "$REPO/systemd/hushkeys-daemon.service" "$SYSTEMD_USER/hushkeys-daemon.service"
green "~/.config/systemd/user/hushkeys-daemon.service"

# ─── 5. Leftovers from the previous name ─────────────────────────────────────
step "5/6 — Leftovers from the previous name"

MIGRATED=0

# The old unit is disabled rather than left alone: two daemons would otherwise
# load the same model and compete for VRAM.
#
# Tested with -L and not -e: once the rename is pulled, the unit symlink points
# at a file that no longer exists, and -e is false on a dangling link — which is
# exactly the case this branch has to handle. Stop is issued separately because
# `disable --now` gives up on a unit whose file has vanished.
if [ -L "$SYSTEMD_USER/dictation-daemon.service" ] || [ -e "$SYSTEMD_USER/dictation-daemon.service" ]; then
    systemctl --user stop dictation-daemon.service >/dev/null 2>&1 || true
    systemctl --user disable dictation-daemon.service >/dev/null 2>&1 || true
    rm -f "$SYSTEMD_USER/dictation-daemon.service"
    green "dictation-daemon.service stopped, disabled and unlinked"
    MIGRATED=1
fi

# Only symlinks are removed, and only those that pointed into this repository
# or now dangle. A real file of the same name is never touched.
for stale in "$HOME/bin/dictee" "$HOME/bin/dictation-daemon"; do
    if [ -L "$stale" ]; then
        target="$(readlink -f -- "$stale" || true)"
        if [ -z "$target" ] || [ ! -e "$target" ] || [ "${target#"$REPO"}" != "$target" ]; then
            rm -f "$stale"
            green "removed stale symlink $stale"
            MIGRATED=1
        else
            warn "$stale points outside this repository — left alone"
        fi
    fi
done

if [ "$MIGRATED" = 1 ]; then
    systemctl --user daemon-reload
fi

for leftover in "$OLD_VENV" "$OLD_CONFIG_DIR"; do
    if [ -e "$leftover" ]; then
        warn "still on disk, remove by hand once hushkeys works: $leftover"
    fi
done
[ "$MIGRATED" = 1 ] || green "nothing left over"

# ─── 6. Services ─────────────────────────────────────────────────────────────
step "6/6 — Services"

systemctl --user daemon-reload

# `enable --now` returns 0 as soon as enabling succeeds, even when the start
# fails behind it. Worse, ydotool.service uses Restart=always: it shows up as
# "active" briefly before dying. So we let the state settle before concluding,
# otherwise we report a false success.
enable_and_check() {
    local unit="$1" hint="$2"
    systemctl --user reset-failed "$unit" >/dev/null 2>&1 || true
    systemctl --user enable --now "$unit" >/dev/null 2>&1 || true
    sleep 3
    if [ "$(systemctl --user is-active "$unit" 2>/dev/null)" = "active" ]; then
        green "$unit active"
    else
        warn "$unit is not active — $hint"
    fi
}

enable_and_check ydotool.service \
    "are you in the 'input' group? membership only takes effect after a reboot"
enable_and_check hushkeys-daemon.service \
    "see: journalctl --user -u hushkeys-daemon"

# Paths are expanded from $REPO rather than hardcoded: the root symlink for the
# CUDA fix is the only one this script does not create itself, and therefore the
# only thing that breaks when the repository is moved.
cat <<EOF

Left to do by hand:

  1. Membership of the input group (access to /dev/uinput), if not done already:
       sudo usermod -aG input $USER      then REBOOT (logging out is not enough)

  2. CUDA-after-suspend fix (laptops only):
       sudo ln -sf $REPO/systemd/nvidia-uvm-reload.service \\
            /etc/systemd/system/nvidia-uvm-reload.service
       sudo systemctl daemon-reload && sudo systemctl enable nvidia-uvm-reload.service

  3. Keyboard shortcut — Settings > Keyboard > Custom Shortcuts:
       Command:  $HOME/bin/hushkeys toggle

EOF
