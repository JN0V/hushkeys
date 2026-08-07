#!/usr/bin/env python3
"""Transcription d'un WAV, sans daemon.

Chemin de repli : recharge le modèle à chaque appel (plusieurs secondes).
Le chemin normal passe par dictation-daemon.py, qui le garde résident.

Usage: transcribe.py <fichier.wav>
"""
import importlib.util
import os
import sys

WAV_FILE = sys.argv[1] if len(sys.argv) > 1 else None
if not WAV_FILE or not os.path.exists(WAV_FILE):
    print(f"Usage: {sys.argv[0]} <fichier.wav>", file=sys.stderr)
    sys.exit(1)

if os.path.getsize(WAV_FILE) < 1000:
    sys.exit(0)


def _load_daemon_module():
    """Charge dictation-daemon.py pour réutiliser sa sélection de device et son
    vocabulaire. Chargement manuel car le nom du fichier porte un tiret et n'est
    donc pas importable par `import`."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dictation-daemon.py")
    spec = importlib.util.spec_from_file_location("dictation_daemon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon_module()

from faster_whisper import WhisperModel  # noqa: E402

MODEL_ID = os.environ.get("DICTATION_MODEL", "medium")
device, compute = daemon.pick_compute_type()

try:
    model = WhisperModel(MODEL_ID, device=device, compute_type=compute)
except Exception:
    model = WhisperModel(MODEL_ID, device="cpu", compute_type="int8")

segments, _info = model.transcribe(
    WAV_FILE,
    language="fr",
    beam_size=5,
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500),
    hotwords=daemon.load_vocabulary(),
)

text = " ".join(seg.text.strip() for seg in segments)
if text:
    print(text)
