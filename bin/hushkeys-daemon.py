#!/usr/bin/env python3
"""hushkeys daemon — keeps the Whisper model resident in VRAM.

Listens on a Unix socket. Protocol: the client sends a WAV path (one UTF-8
line), the daemon answers with the transcribed text — or, when transcription
fails, with a line starting with ERROR_PREFIX. An empty answer means "nothing
heard" and nothing else: a failure must never be mistaken for silence.

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
import wave

SOCKET_PATH = "/tmp/hushkeys-daemon.sock"
PID_FILE = "/tmp/hushkeys-daemon.pid"
MODEL_ID = os.environ.get("HUSHKEYS_MODEL", "medium")
VOCAB_FILE = os.environ.get(
    "HUSHKEYS_VOCAB_FILE", os.path.expanduser("~/.config/hushkeys/vocabulary.txt")
)

# Marks a failure in the daemon's answer. \x01 is a control character: it never
# appears in a transcription, so it cannot be confused with dictated text.
ERROR_PREFIX = "\x01ERROR "


def transcribe_options():
    """Decoding options, shared by the daemon and the standalone fallback.

    beam_size and condition_on_previous_text are not quality knobs here, they
    are VRAM knobs. Whisper decodes 30-second windows; with
    condition_on_previous_text, each window is prompted with the previous
    ~220 tokens, and the cross-attention over those 220 tokens against the
    1500 encoder frames, times the beam width, is what blows up. Measured on a
    2 GB MX230 with medium/int8 (~960 MiB resident, so ~1 GB of headroom), on
    real French speech:

        beam=5, conditioning on   ->  2 min of speech peaks at 1964 MiB : OOM
        beam=5, conditioning off  -> 10 min of speech peaks at 1548 MiB : OOM
        beam=2, conditioning off  ->  1 h  of speech peaks at 1356 MiB : OK

    Without the conditioning the peak stops depending on the length of the
    recording altogether: an hour of speech costs what two minutes cost. An OOM
    is not recoverable either — see the daemon loop — so the point is to stay
    inside the envelope rather than gamble on how long the dictation runs.

    Dropping the conditioning costs little on dictation, where each 30-second
    window is largely self-contained, and it removes the repetition loops it
    can trigger. beam_size=2 rather than 1: at 1 the decoder starts missing
    proper nouns at window boundaries — the very context the conditioning used
    to supply — and the second beam buys that back for 32 MiB.
    """
    return dict(
        language="fr",
        beam_size=2,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )


def wav_duration(path):
    """Length of the recording in seconds, read from the WAV header."""
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def is_cuda_failure(exc):
    """Whether the exception left the CUDA context unusable.

    ctranslate2 surfaces these as plain RuntimeError, so the message is all we
    have to go on.
    """
    message = str(exc).lower()
    return any(k in message for k in ("cuda", "cublas", "cudnn"))


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


def send(conn, payload):
    """Answer the client and close the connection, whatever happens next."""
    try:
        conn.sendall((payload + "\n").encode("utf-8"))
    finally:
        conn.close()


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
                send(conn, "PONG")
                continue

            wav_path = data
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
                # Too short to hold anything: a genuine "nothing heard".
                send(conn, "")
                continue

            t0 = time.time()
            duration = wav_duration(wav_path)
            try:
                segments, _info = model.transcribe(
                    wav_path, hotwords=load_vocabulary(), **transcribe_options()
                )
                text = " ".join(seg.text.strip() for seg in segments)
            except Exception as e:
                # Never let a failure reach the client as an empty answer: it
                # would show up as "nothing heard" and hide the real cause.
                # One line: the answer is read line-wise on the other side.
                reason = " ".join(f"{type(e).__name__}: {e}".split())
                print(
                    f"Transcription failed after {time.time()-t0:.1f}s "
                    f"on {duration:.0f}s of audio: {reason}",
                    file=sys.stderr,
                    flush=True,
                )
                send(conn, ERROR_PREFIX + reason)

                # A CUDA error is terminal: the context is gone, and every
                # later request fails with "invalid device ordinal". Restarting
                # is the only way back, so exit non-zero and let systemd
                # (Restart=on-failure) reload the model.
                if is_cuda_failure(e):
                    print(
                        "CUDA context lost — exiting so the daemon is restarted.",
                        file=sys.stderr,
                        flush=True,
                    )
                    server.close()
                    cleanup()
                    os._exit(1)
                continue

            print(
                f"Transcribed {duration:.0f}s of audio in {time.time()-t0:.1f}s: "
                f"{text[:80]}...",
                flush=True,
            )
            send(conn, text)
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
