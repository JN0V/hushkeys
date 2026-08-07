#!/bin/bash
# Installation par liens symboliques : rien n'est copié hors de ~/.config/dictation,
# qui reçoit la configuration personnelle. Un `git pull` met donc l'installation à jour.

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/dictation-venv"
CONFIG_DIR="$HOME/.config/dictation"
SYSTEMD_USER="$HOME/.config/systemd/user"

green() { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '\033[33m!\033[0m %s\n' "$1"; }
step()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ─── 1. Dépendances système ──────────────────────────────────────────────────
step "1/5 — Dépendances système"

MISSING=()
for cmd in parecord socat notify-send ydotool ydotoold; do
    command -v "$cmd" >/dev/null 2>&1 || MISSING+=("$cmd")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    warn "Manquant : ${MISSING[*]}"
    echo "    sudo apt install -y pulseaudio-utils socat libnotify-bin ydotool"
    exit 1
fi
green "parecord, socat, notify-send, ydotool présents"

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    warn "uv absent — nécessaire pour installer un CPython dédié."
    echo "    Voir README, section « Pourquoi uv et pas le Python système »."
    exit 1
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
green "uv présent"

# ─── 2. Environnement Python ─────────────────────────────────────────────────
step "2/5 — Environnement Python"

if [ -x "$VENV/bin/python" ]; then
    green "venv déjà présent ($VENV)"
else
    # Python 3.12 et non le Python système : ctranslate2 ne publie pas encore de
    # wheels pour 3.14, livré par Ubuntu 26.04.
    "$UV" python install 3.12
    "$UV" venv --python 3.12 "$VENV"
    VIRTUAL_ENV="$VENV" "$UV" pip install faster-whisper
    green "faster-whisper installé"
fi

if [ -d "$VENV"/lib/python*/site-packages/nvidia ]; then
    green "bibliothèques CUDA présentes"
elif command -v nvidia-smi >/dev/null 2>&1; then
    VIRTUAL_ENV="$VENV" "$UV" pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12
    green "bibliothèques CUDA installées"
else
    warn "Pas de GPU NVIDIA détecté — fonctionnement en CPU int8."
fi

# ─── 3. Configuration personnelle ────────────────────────────────────────────
step "3/5 — Configuration"

mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/vocabulary.txt" ]; then
    green "vocabulaire existant conservé"
else
    cp "$REPO/config/vocabulary.txt.example" "$CONFIG_DIR/vocabulary.txt"
    green "vocabulaire initialisé depuis le modèle"
fi

# ─── 4. Liens symboliques ────────────────────────────────────────────────────
step "4/5 — Liens symboliques"

mkdir -p "$HOME/bin" "$SYSTEMD_USER"
ln -sfn "$REPO/bin/dictee" "$HOME/bin/dictee"
green "~/bin/dictee"
# Le service pointe sur ~/bin/dictation-daemon plutôt que sur le dépôt : c'est ce
# qui rend le unit indépendant de l'endroit où le dépôt est cloné.
ln -sfn "$REPO/bin/dictation-daemon" "$HOME/bin/dictation-daemon"
green "~/bin/dictation-daemon"
ln -sfn "$REPO/systemd/dictation-daemon.service" "$SYSTEMD_USER/dictation-daemon.service"
green "~/.config/systemd/user/dictation-daemon.service"

# ─── 5. Services ─────────────────────────────────────────────────────────────
step "5/5 — Services"

systemctl --user daemon-reload

# `enable --now` renvoie 0 dès que l'activation réussit, même si le démarrage
# échoue derrière. Pire, ydotool.service est en Restart=always : il apparaît
# brièvement « active » avant de mourir. On laisse donc l'état se stabiliser
# avant de conclure, sinon on affiche un faux succès.
enable_and_check() {
    local unit="$1" hint="$2"
    systemctl --user reset-failed "$unit" >/dev/null 2>&1 || true
    systemctl --user enable --now "$unit" >/dev/null 2>&1 || true
    sleep 3
    if [ "$(systemctl --user is-active "$unit" 2>/dev/null)" = "active" ]; then
        green "$unit actif"
    else
        warn "$unit n'est pas actif — $hint"
    fi
}

enable_and_check ydotool.service \
    "es-tu dans le groupe 'input' ? l'appartenance ne prend effet qu'après reconnexion"
enable_and_check dictation-daemon.service \
    "voir : journalctl --user -u dictation-daemon"

cat <<'EOF'

Reste à faire à la main :

  1. Appartenance au groupe input (accès à /dev/uinput), si ce n'est pas déjà fait :
       sudo usermod -aG input $USER      puis se déconnecter/reconnecter

  2. Correctif CUDA après veille (portable uniquement) :
       sudo ln -sf ~/Documents/dev/dictation/systemd/nvidia-uvm-reload.service \
            /etc/systemd/system/nvidia-uvm-reload.service
       sudo systemctl daemon-reload && sudo systemctl enable nvidia-uvm-reload.service

  3. Raccourci clavier — Settings > Keyboard > Custom Shortcuts :
       Commande :  /home/<user>/bin/dictee toggle

EOF
