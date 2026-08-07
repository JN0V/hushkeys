#!/bin/bash
# Environnement partagé par dictee et dictation-daemon.
# Sourcé, jamais exécuté directement.

VENV="${DICTATION_VENV:-$HOME/.local/share/dictation-venv}"
CONFIG_DIR="${DICTATION_CONFIG_DIR:-$HOME/.config/dictation}"
VOCAB_FILE="$CONFIG_DIR/vocabulary.txt"

SOCKET="/tmp/dictation-daemon.sock"
PIDFILE="/tmp/dictation.pid"
WAVFILE="/tmp/dictation_recording.wav"

# Modèle par défaut : medium.
# large-v3 pèse ~1,6 Go en int8 et ne laisse pas de marge sur un GPU 2 Go.
export DICTATION_MODEL="${DICTATION_MODEL:-medium}"
export DICTATION_VOCAB_FILE="$VOCAB_FILE"

# Les bibliothèques CUDA viennent des wheels pip (nvidia-*-cu12), pas du système.
# La version de Python n'est pas écrite en dur : le venv est reconstructible
# avec une autre version sans qu'il faille modifier ce fichier ni les units.
NVIDIA_BASE=$(echo "$VENV"/lib/python*/site-packages/nvidia)
if [ -d "$NVIDIA_BASE" ]; then
    export LD_LIBRARY_PATH="$NVIDIA_BASE/cublas/lib:$NVIDIA_BASE/cudnn/lib:$NVIDIA_BASE/cuda_runtime/lib:${LD_LIBRARY_PATH:-}"
fi
