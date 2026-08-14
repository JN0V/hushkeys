#!/usr/bin/env python3
"""hushkeys daemon — keeps the Whisper model resident in VRAM.

Listens on a Unix socket. Protocol: the client sends a WAV path (one UTF-8
line), the daemon answers with the transcribed text.

Usage:
  hushkeys-daemon.py              start the daemon
  hushkeys-daemon.py --status     report whether it is running
  hushkeys-daemon.py --stop       stop it
"""
import os
import sys
import socket
import signal
import time

SOCKET_PATH = "/tmp/hushkeys-daemon.sock"
PID_FILE = "/tmp/hushkeys-daemon.pid"
MODEL_ID = os.environ.get("HUSHKEYS_MODEL", "medium")
VOCAB_FILE = os.environ.get(
    "HUSHKEYS_VOCAB_FILE", os.path.expanduser("~/.config/hushkeys/vocabulary.txt")
)


def load_vocabulary():
    """Technical vocabulary handed to the decoder through `hotwords`.

    Re-read on every transcription: editing the file takes effect without
    restarting the daemon (the model stays loaded).
    """
    try:
        with open(VOCAB_FILE, encoding="utf-8") as f:
            terms = [
                line.strip()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]
        return ", ".join(terms) if terms else None
    except FileNotFoundError:
        return None


def pick_compute_type():
    """Pick the best compute type the GPU actually supports.

    float16 requires compute capability >= 7.0. On Pascal (MX230, CC 6.1)
    ctranslate2 refuses float16 outright; int8 works there through dp4a.
    We ask ctranslate2 rather than guess: the same checkout then runs on an
    MX230 (int8) and on an RTX 2070 (float16) with no change.
    """
    try:
        import ctranslate2

        supported = ctranslate2.get_supported_compute_types("cuda")
        for candidate in ("float16", "int8"):
            if candidate in supported:
                return "cuda", candidate
    except Exception:
        pass
    return "cpu", "int8"


def is_running():
    if not os.path.exists(PID_FILE):
        return False
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, PermissionError):
        cleanup()
        return False


def cleanup():
    for f in [SOCKET_PATH, PID_FILE]:
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass


def stop_daemon():
    if not os.path.exists(PID_FILE):
        print("Daemon not running.")
        return
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Daemon (PID {pid}) stopped.")
    except (ProcessLookupError, ValueError):
        print("Daemon not running (stale PID file).")
    cleanup()


def start_daemon():
    if is_running():
        print("Daemon already running.")
        sys.exit(0)

    cleanup()

    device, compute = pick_compute_type()
    print(f"Loading model '{MODEL_ID}' on {device} ({compute})...", flush=True)
    t0 = time.time()

    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(MODEL_ID, device=device, compute_type=compute)
    except Exception as e:
        print(f"Failed on {device}/{compute} ({e}) — falling back to CPU int8.", file=sys.stderr)
        device, compute = "cpu", "int8"
        model = WhisperModel(MODEL_ID, device=device, compute_type=compute)

    print(f"Model loaded on {device} ({compute}) in {time.time()-t0:.1f}s", flush=True)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    os.chmod(SOCKET_PATH, 0o600)

    def handle_signal(signum, frame):
        print("\nShutting down.", flush=True)
        server.close()
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    print(f"Daemon ready, listening on {SOCKET_PATH}", flush=True)

    while True:
        conn = None
        try:
            conn, _ = server.accept()
            data = conn.recv(4096).decode("utf-8").strip()

            if data == "PING":
                conn.sendall(b"PONG\n")
                conn.close()
                continue

            wav_path = data
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
                conn.sendall(b"\n")
                conn.close()
                continue

            t0 = time.time()
            segments, _info = model.transcribe(
                wav_path,
                language="fr",
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                hotwords=load_vocabulary(),
            )
            text = " ".join(seg.text.strip() for seg in segments)
            print(f"Transcribed in {time.time()-t0:.1f}s: {text[:80]}...", flush=True)

            conn.sendall((text + "\n").encode("utf-8"))
            conn.close()
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr, flush=True)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop_daemon()
    elif "--status" in sys.argv:
        if is_running():
            print(f"Daemon running (PID {open(PID_FILE).read().strip()})")
        else:
            print("Daemon not running")
    else:
        start_daemon()
