#!/bin/bash
# Environment shared by hushkeys and hushkeys-daemon.
# Sourced, never executed directly.

VENV="${HUSHKEYS_VENV:-$HOME/.local/share/hushkeys-venv}"
CONFIG_DIR="${HUSHKEYS_CONFIG_DIR:-$HOME/.config/hushkeys}"
VOCAB_FILE="$CONFIG_DIR/vocabulary.txt"

SOCKET="/tmp/hushkeys-daemon.sock"
PIDFILE="/tmp/hushkeys.pid"
WAVFILE="/tmp/hushkeys-recording.wav"
# Kept when transcription fails, so the failure can be replayed.
FAILED_WAV="/tmp/hushkeys-failed.wav"

# Per-machine settings (language, model): environment first, then the config
# file, then the defaults. The file holds bare key=value lines — see
# config/config.example — so it is sourced, and its keys read below.
language=""; model=""
if [ -f "$CONFIG_DIR/config" ]; then
    # shellcheck source=/dev/null
    source "$CONFIG_DIR/config"
fi
# "auto": detected on each dictation. Any Whisper language code pins it.
export HUSHKEYS_LANGUAGE="${HUSHKEYS_LANGUAGE:-${language:-auto}}"
# medium: large-v3 weighs ~1.6 GB in int8 and leaves no headroom on a 2 GB GPU.
export HUSHKEYS_MODEL="${HUSHKEYS_MODEL:-${model:-medium}}"
export HUSHKEYS_VOCAB_FILE="$VOCAB_FILE"
unset language model

# CUDA libraries come from the pip wheels (nvidia-*-cu12), not from the system.
# The Python version is never hardcoded: the venv can be rebuilt against another
# version without editing this file or the systemd units.
NVIDIA_BASE=$(echo "$VENV"/lib/python*/site-packages/nvidia)
if [ -d "$NVIDIA_BASE" ]; then
    export LD_LIBRARY_PATH="$NVIDIA_BASE/cublas/lib:$NVIDIA_BASE/cudnn/lib:$NVIDIA_BASE/cuda_runtime/lib:${LD_LIBRARY_PATH:-}"
fi
