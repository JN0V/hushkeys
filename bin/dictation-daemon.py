#!/usr/bin/env python3
"""Daemon de dictée — garde le modèle Whisper résident en VRAM.

Écoute sur une socket Unix. Protocole : le client envoie un chemin de WAV
(ligne UTF-8), le daemon répond par le texte transcrit.

Usage:
  dictation-daemon.py              démarre le daemon
  dictation-daemon.py --status     indique s'il tourne
  dictation-daemon.py --stop       l'arrête
"""
import os
import sys
import socket
import signal
import time

SOCKET_PATH = "/tmp/dictation-daemon.sock"
PID_FILE = "/tmp/dictation-daemon.pid"
MODEL_ID = os.environ.get("DICTATION_MODEL", "medium")
VOCAB_FILE = os.environ.get(
    "DICTATION_VOCAB_FILE", os.path.expanduser("~/.config/dictation/vocabulary.txt")
)


def load_vocabulary():
    """Vocabulaire technique passé au décodeur via `hotwords`.

    Relu à chaque transcription : éditer le fichier prend effet sans
    redémarrer le daemon (le modèle reste chargé).
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
    """Choisit le meilleur type de calcul réellement supporté par le GPU.

    float16 exige une compute capability >= 7.0. Sur Pascal (MX230, CC 6.1)
    ctranslate2 refuse explicitement float16 ; int8 y fonctionne via dp4a.
    On interroge ctranslate2 plutôt que de deviner : le même dépôt tourne
    ainsi sur MX230 (int8) et sur RTX 2070 (float16) sans modification.
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
        print("Daemon non démarré.")
        return
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Daemon (PID {pid}) arrêté.")
    except (ProcessLookupError, ValueError):
        print("Daemon non démarré (fichier PID orphelin).")
    cleanup()


def start_daemon():
    if is_running():
        print("Daemon déjà démarré.")
        sys.exit(0)

    cleanup()

    device, compute = pick_compute_type()
    print(f"Chargement du modèle '{MODEL_ID}' sur {device} ({compute})...", flush=True)
    t0 = time.time()

    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(MODEL_ID, device=device, compute_type=compute)
    except Exception as e:
        print(f"Échec sur {device}/{compute} ({e}) — repli CPU int8.", file=sys.stderr)
        device, compute = "cpu", "int8"
        model = WhisperModel(MODEL_ID, device=device, compute_type=compute)

    print(f"Modèle chargé sur {device} ({compute}) en {time.time()-t0:.1f}s", flush=True)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    os.chmod(SOCKET_PATH, 0o600)

    def handle_signal(signum, frame):
        print("\nArrêt du daemon.", flush=True)
        server.close()
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    print(f"Daemon prêt, à l'écoute sur {SOCKET_PATH}", flush=True)

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
            print(f"Transcrit en {time.time()-t0:.1f}s : {text[:80]}...", flush=True)

            conn.sendall((text + "\n").encode("utf-8"))
            conn.close()
        except Exception as e:
            print(f"Erreur : {e}", file=sys.stderr, flush=True)
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
            print(f"Daemon démarré (PID {open(PID_FILE).read().strip()})")
        else:
            print("Daemon non démarré")
    else:
        start_daemon()
