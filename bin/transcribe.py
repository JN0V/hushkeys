#!/usr/bin/env python3
"""Transcribe one WAV file, without the daemon.

Fallback path: reloads the model on every call (several seconds). The normal
path goes through hushkeys-daemon.py, which keeps it resident.

Usage: transcribe.py <file.wav>
"""
import importlib.util
import os
import sys

WAV_FILE = sys.argv[1] if len(sys.argv) > 1 else None
if not WAV_FILE or not os.path.exists(WAV_FILE):
    print(f"Usage: {sys.argv[0]} <file.wav>", file=sys.stderr)
    sys.exit(1)

if os.path.getsize(WAV_FILE) < 1000:
    sys.exit(0)


def _load_daemon_module():
    """Load hushkeys-daemon.py to reuse its device selection, vocabulary and
    decoding options — those must not drift between the two paths.
    Loaded by hand because the file name contains a dash and is therefore not
    importable with `import`."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hushkeys-daemon.py")
    spec = importlib.util.spec_from_file_location("hushkeys_daemon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon_module()

from faster_whisper import WhisperModel  # noqa: E402

MODEL_ID = os.environ.get("HUSHKEYS_MODEL", "medium")
device, compute = daemon.pick_compute_type()

try:
    model = WhisperModel(MODEL_ID, device=device, compute_type=compute)
except Exception:
    model = WhisperModel(MODEL_ID, device="cpu", compute_type="int8")

segments, _info = model.transcribe(
    WAV_FILE, hotwords=daemon.load_vocabulary(), **daemon.transcribe_options()
)

text = " ".join(seg.text.strip() for seg in segments)
if text:
    print(text)
