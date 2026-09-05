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
# A Whisper language code, or "auto" — see transcribe_file for what auto does.
LANGUAGE = os.environ.get("HUSHKEYS_LANGUAGE", "auto").strip().lower() or "auto"
SAMPLE_RATE = 16000
# Longest piece handed to one transcribe() call — see split_on_silence.
CHUNK_SECONDS = 20.0
# Words of the running transcription handed to the next piece — see transcribe_file.
CONTEXT_WORDS = 40
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

    Dropping the conditioning costs little on dictation, where each 30-second
    window is largely self-contained, and it removes the repetition loops it
    can trigger. beam_size=2 rather than 1: at 1 the decoder starts missing
    proper nouns at window boundaries — the very context the conditioning used
    to supply — and the second beam buys that back for 32 MiB.

    Those measurements were taken without `hotwords`, which the daemon always
    passes — see split_on_silence for why that matters, and why the VAD runs
    there rather than here.

    best_of is the same knob as beam_size, for the path nobody looks at. When a
    window fails the compression-ratio or log-probability threshold,
    faster-whisper retries it by sampling, and best_of defaults to 5 — so the
    fallback quietly decodes 2.5x wider than the beam it is replacing. That is
    the discrete jump behind every erratic OOM here: a repetition loop fills the
    window to its 448-token ceiling ("Compression ratio threshold is not met
    with temperature 0.0 (27.045455 > 2.400000)"), and the retry then decodes
    those tokens five candidates wide.

    It explains what nothing else did — why 4 hotwords failed where 12 passed,
    and why a 20 s piece could die where a 25 s one lived. The loop depends on
    what is being said, not on how long it is. With best_of=2 every
    configuration that used to OOM completes, including a 28 s piece, which is
    the point: the failure was never really about size.
    """
    return dict(
        language=resolve_language(),
        beam_size=2,
        best_of=2,
        condition_on_previous_text=False,
        # The VAD already ran, in split_on_silence: running it again here would
        # only re-scan audio that is speech by construction.
        vad_filter=False,
    )


def resolve_language():
    """The language handed to the decoder: a code, or None to detect it.

    A code Whisper does not know would only surface at the first dictation,
    as a ValueError swallowed into a failed transcription; a typo in the
    config file must not cost a dictation. Checked here, on every call, so
    that the fallback and the daemon agree and the warning is in the log.
    """
    if LANGUAGE == "auto":
        return None
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    if LANGUAGE in _LANGUAGE_CODES:
        return LANGUAGE
    print(
        f"Unknown language '{LANGUAGE}' (expected a Whisper code such as fr, en, de, "
        "or auto) — detecting it instead.",
        file=sys.stderr, flush=True,
    )
    return None


def split_on_silence(audio):
    """Cut `audio` into pieces of at most CHUNK_SECONDS, at silences.

    Turning off condition_on_previous_text was supposed to cap the decoder
    prompt, and it does — but `hotwords` reopens the very same branch:

        # faster_whisper/transcribe.py
        if previous_tokens or (hotwords and not prefix):
            prompt.append(tokenizer.sot_prev)

    So the vocabulary silently undoes the cap above, and on a 2 GB MX230 a
    dictation past one 30-second window dies with "CUDA failed with error out
    of memory". Same 38 s recording, same options, only hotwords differing:

        without the vocabulary          -> 11.6 s, 8 segments : OK
        with the 17 terms of vocabulary -> OOM on the 2nd window

    Reproducible in both directions, and not a matter of prompt length: 4 terms
    fail where 12 pass, deterministically, because the prompt changes which
    tokens get decoded rather than just how many. Trimming the vocabulary is a
    coin toss, not a fix.

    Cutting is. One transcribe() call per piece bounds what any single call has
    to decode, so the peak stops depending on the length of the dictation: 8 min
    of real speech with the full vocabulary peaks at 1324 MiB, exactly what
    164 s costs.

    The cut lands on silence rather than on a stopwatch — max_speech_duration_s
    splits at the last silence over 100 ms — because cutting at a flat interval
    slices through a word, and both halves then decode into something neither of
    them was.

    CHUNK_SECONDS is not "just under the 30-second window": the silences are
    dropped, so a piece is 20 s of *dense* speech, worth far more decoded tokens
    than any natural 30 s of dictation. It is those tokens, against the 1500
    encoder frames and the beam width, that are allocated — so the piece has to
    be short enough that a full one still fits. Measured on the MX230, on 164 s
    of real French dictation:

        CHUNK_SECONDS = 28  ->  1484 MiB : OOM
        CHUNK_SECONDS = 24  ->  1356 MiB : OK
        CHUNK_SECONDS = 22  ->  1324 MiB : OK
        CHUNK_SECONDS = 20  ->  1324 MiB : OK

    20 rather than 24 to sit two steps below the cliff rather than one, and
    because the peak has plateaued by then: shorter pieces buy no further
    margin. It holds over length — 8 min of the same speech also peaks at
    1324 MiB — and costs almost nothing in time (55.5 s against 53.8 s at 24).
    """
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    speech = get_speech_timestamps(
        audio,
        VadOptions(
            min_silence_duration_ms=500, max_speech_duration_s=CHUNK_SECONDS
        ),
        sampling_rate=SAMPLE_RATE,
    )

    limit = int(CHUNK_SECONDS * SAMPLE_RATE)
    chunks, current, length = [], [], 0
    for window in speech:
        piece = audio[window["start"] : window["end"]]
        if current and length + len(piece) > limit:
            chunks.append(np.concatenate(current))
            current, length = [], 0
        current.append(piece)
        length += len(piece)
    if current:
        chunks.append(np.concatenate(current))
    return chunks


def transcribe_file(model, wav_path):
    """Transcribe a recording of any length. Empty means nothing was heard.

    Each piece is decoded cold, which shows at the seams when the cut had to
    fall mid-sentence — the decoder picks up with no idea what preceded. On a
    real 164 s dictation containing 56 s without a single pause, one seam came
    back with its clause trailing off into an ellipsis: the qualifier the
    speaker had used was gone, and the sentence read as cut off mid-thought.

    So the tail of what has been transcribed so far is handed to the next piece
    as initial_prompt. That is the context condition_on_previous_text used to
    supply, except bounded: 40 words, not a window that grows with the
    recording.

    The alternative — repeating a few seconds of audio at the head of each piece
    and stitching the texts — reads better at the seam but silently drops
    speech, and worse the more it repeats:

        overlap 3 s  -> loses one word
        overlap 5 s  -> loses eight, a whole enumeration
        overlap 7 s  -> loses more still

    The words are never decoded at all, so no amount of care in the stitching
    buys them back. Measured against the same baseline, 40 words of context drop
    **nothing** and restore the missing qualifier. A ragged seam is cosmetic; a
    dictation missing a phrase the speaker said is not. Hence this, and not
    that.

    Language: with LANGUAGE == "auto", faster-whisper detects it from the first
    piece — that costs nothing, the encoder pass is shared with the decoding —
    and the pieces that follow are pinned to it. Left undetected, each piece
    would be guessed on its own, and a short piece of a French dictation can
    come back as Italian, or as English with the words "translated". One
    language per dictation, then; a new dictation detects afresh.
    """
    from faster_whisper.audio import decode_audio

    audio = decode_audio(wav_path, sampling_rate=SAMPLE_RATE)
    hotwords = load_vocabulary()
    options = transcribe_options()

    parts = []
    for chunk in split_on_silence(audio):
        context = " ".join(" ".join(parts).split()[-CONTEXT_WORDS:]) if parts else None
        segments, info = model.transcribe(
            chunk, hotwords=hotwords, initial_prompt=context, **options
        )
        text = " ".join(seg.text.strip() for seg in segments)
        if options["language"] is None:
            options["language"] = info.language
            print(
                f"Language detected: {info.language} ({info.language_probability:.2f})",
                flush=True,
            )
        if text:
            parts.append(text)
    return " ".join(parts)


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
    print(f"Language: {resolve_language() or 'detected per dictation'}", flush=True)

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
                text = transcribe_file(model, wav_path)
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
