#!/usr/bin/env python3
"""hushkeys bench — measure this machine, and say which settings fit it.

    hushkeys bench                  read the passage aloud, then measure
    hushkeys bench --wav file.wav   measure on an existing recording
    hushkeys bench --models small,medium,large-v3 --device cpu
    hushkeys reports                rebuild reports/README.md from reports/*.json

Each model runs in its own subprocess: VRAM is released for certain between
runs, and a lost CUDA context — which is what an out-of-memory leaves behind —
cannot poison the next run. The subprocess goes through the daemon's own
transcribe_file, so the numbers are those of a real dictation: same chunking,
same decoding options, same vocabulary.

The report lands in reports/<label>-<date>.json (the numbers) and .md (the
numbers plus a checklist to fill in by hand: pasting, indicator, suspend).
Commit both. The label names the hardware (the card, or the CPU without
one), never the machine: a hostname is the kind of thing that must not end
up in a public repository. --label overrides it. The recording itself stays
out of the repository, under ~/.local/share/hushkeys/bench/.
"""
import argparse
import datetime as dt
import difflib
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REPORTS = os.path.join(REPO, "reports")
BENCH_DIR = os.path.expanduser("~/.local/share/hushkeys/bench")
DEFAULT_MODELS = "small,medium,large-v3"
SCHEMA = 1


# ─── Machine facts ───────────────────────────────────────────────────────────

def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def os_release():
    try:
        for line in open("/etc/os-release"):
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def cpu_model():
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
    except OSError:
        pass
    return platform.processor()


def ram_gib():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return None


def gpu_facts():
    out = sh(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    if not out:
        return None
    name, total, driver = [x.strip() for x in out.splitlines()[0].split(",")]
    return {"name": name, "vram_mib": int(float(total)), "driver": driver}


def vram_used_mib():
    out = sh(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    try:
        return int(float(out.splitlines()[0]))
    except (ValueError, IndexError):
        return None


def hardware_label(gpu, cpu_only=False):
    """What the report is filed under: "mx230", "rtx-2070", "cpu-i7-10510u"."""
    if gpu and not cpu_only:
        name = re.sub(r"^(NVIDIA|GeForce|RTX|GTX|Quadro|Tesla)\s+", "", gpu["name"], flags=re.I)
        name = re.sub(r"^(NVIDIA|GeForce)\s+", "", name, flags=re.I)
    else:
        m = re.search(r"(i[3579]-\w+|Ryzen \d \w+|Core Ultra \d \w+|Xeon \w+)", cpu_model())
        name = "cpu-" + (m.group(1) if m else cpu_model().split()[0])
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "machine"


def host_facts():
    return {
        "os": os_release(),
        "kernel": platform.release(),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
        "session": os.environ.get("XDG_SESSION_TYPE", ""),
        "gnome_shell": sh(["gnome-shell", "--version"]),
        "cpu": cpu_model(),
        "ram_gib": ram_gib(),
    }


def software_facts(venv_python):
    code = (
        "import sys, faster_whisper, ctranslate2, glob, os;"
        "print(sys.version.split()[0]); print(faster_whisper.__version__); print(ctranslate2.__version__);"
        "print(bool(glob.glob(os.path.join(sys.prefix, 'lib/python*/site-packages/nvidia'))))"
    )
    out = sh([venv_python, "-c", code]).splitlines()
    keys = ["python", "faster_whisper", "ctranslate2", "cuda_wheels"]
    return dict(zip(keys, out)) if len(out) == 4 else {}


def hushkeys_commit():
    return sh(["git", "-C", REPO, "rev-parse", "--short", "HEAD"])


# ─── One run, in a subprocess ────────────────────────────────────────────────

def load_daemon_module():
    """The daemon holds the decoding options, chunking and vocabulary; the
    bench reuses them so the numbers are those of a dictation."""
    path = os.path.join(HERE, "hushkeys-daemon.py")
    spec = importlib.util.spec_from_file_location("hushkeys_daemon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VramSampler(threading.Thread):
    """Peak of nvidia-smi's memory.used while running — the whole card, which
    is what an OOM is measured against, not this process alone."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak = None
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            used = vram_used_mib()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used
            self._halt.wait(0.25)

    def stop(self):
        self._halt.set()
        self.join(timeout=2)


def run_one(model_id, wav, passage_text):
    """Load one model, transcribe one file, print the measurements as JSON."""
    daemon = load_daemon_module()
    from faster_whisper import WhisperModel

    device, compute = daemon.pick_compute_type()
    result = {"model": model_id, "device": device, "compute": compute, "status": "ok", "error": None}
    result["vram_baseline_mib"] = vram_used_mib() if device == "cuda" else None
    sampler = VramSampler() if device == "cuda" else None
    if sampler:
        sampler.start()
    try:
        t0 = time.time()
        model = WhisperModel(model_id, device=device, compute_type=compute)
        result["load_s"] = round(time.time() - t0, 1)
        t0 = time.time()
        text = daemon.transcribe_file(model, wav)
        result["transcribe_s"] = round(time.time() - t0, 1)
        result["text"] = text
        result["agreement"] = word_agreement(passage_text, text) if passage_text else None
    except Exception as e:  # an OOM, a missing model, a lost context
        result["status"] = "failed"
        result["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        if sampler:
            sampler.stop()
            result["vram_peak_mib"] = sampler.peak
    print(json.dumps(result, ensure_ascii=False))


def word_agreement(reference, hypothesis):
    """Share of words in common, order kept: 1.0 means identical."""
    norm = lambda s: re.sub(r"[^\w\s]", " ", s.lower()).split()
    a, b = norm(reference), norm(hypothesis)
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


# ─── Recording ───────────────────────────────────────────────────────────────

def record(passage_text, out_path):
    print("\nRead this aloud, at dictation pace. Press Enter to start, Enter again when done.\n")
    print(passage_text)
    input()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    proc = subprocess.Popen(
        ["parecord", "--channels=1", "--rate=16000", "--format=s16le", "--file-format=wav", out_path]
    )
    print("Recording... ", end="", flush=True)
    input()
    proc.terminate()
    proc.wait()
    time.sleep(0.2)
    print(f"saved to {out_path}")
    return out_path


def wav_seconds(path):
    with wave.open(path, "rb") as w:
        return round(w.getnframes() / float(w.getframerate()), 1)


# ─── The bench itself ────────────────────────────────────────────────────────

def daemon_active():
    return sh(["systemctl", "--user", "is-active", "hushkeys-daemon"]) == "active"


def recommend(gpu, runs):
    """Which model to put in ~/.config/hushkeys/config, and why.

    On a GPU: the largest model that completed with its peak under 80 % of
    the card — the remaining fifth is the headroom the decoding needs on a
    longer dictation than the passage. Without one: the largest model that
    keeps up with speech (transcription shorter than the recording), since
    on the CPU the wait is the only cost.
    """
    ok = [r for r in runs if r["status"] == "ok"]
    if not ok:
        return {"model": None, "reason": "no configuration completed"}
    order = {"small": 0, "medium": 1, "large-v3": 2}
    ok.sort(key=lambda r: order.get(r["model"], 99))
    if gpu:
        fits = [r for r in ok if r["device"] == "cuda" and r.get("vram_peak_mib")
                and r["vram_peak_mib"] <= 0.8 * gpu["vram_mib"]]
        if fits:
            r = fits[-1]
            return {"model": r["model"], "reason": f"peak {r['vram_peak_mib']} MiB of {gpu['vram_mib']} on the GPU"}
        return {"model": ok[0]["model"], "reason": "the only model with headroom is the smallest one"}
    keeps_up = [r for r in ok if r.get("rtf") is not None and r["rtf"] < 1.0]
    r = (keeps_up or ok)[-1] if keeps_up else ok[0]
    return {"model": r["model"], "reason": f"CPU only, {r['transcribe_s']} s for the passage (RTF {r['rtf']})"}


def bench(args):
    venv_python = sys.executable
    passage_path = os.path.join(REPO, "bench", f"passage.{args.passage}.txt")
    passage_text = open(passage_path).read().strip() if os.path.exists(passage_path) else None

    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    wav = args.wav or record(passage_text, os.path.join(BENCH_DIR, f"{stamp}.wav"))
    seconds = wav_seconds(wav)

    gpu = gpu_facts()
    if args.device == "cpu":
        os.environ["HUSHKEYS_DEVICE"] = "cpu"
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    restart_daemon = daemon_active()
    if restart_daemon:
        print("Stopping hushkeys-daemon for the duration of the bench (it holds the VRAM).")
        subprocess.run(["systemctl", "--user", "stop", "hushkeys-daemon"])

    runs = []
    try:
        for model_id in models:
            print(f"\n=== {model_id} ===", flush=True)
            proc = subprocess.run(
                [venv_python, __file__, "--run-one", model_id, wav, passage_path if passage_text else ""],
                capture_output=True, text=True,
            )
            lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
            if not lines:
                runs.append({"model": model_id, "status": "failed",
                             "error": (proc.stderr.strip().splitlines() or ["no output"])[-1][:300]})
            else:
                r = json.loads(lines[-1])
                r["rtf"] = round(r["transcribe_s"] / seconds, 2) if r.get("transcribe_s") and seconds else None
                runs.append(r)
            r = runs[-1]
            if r["status"] == "ok":
                print(f"load {r['load_s']} s · transcription {r['transcribe_s']} s (RTF {r['rtf']})"
                      f" · peak VRAM {r.get('vram_peak_mib')} MiB · agreement {r.get('agreement')}")
                print(f"  {r['text'][:160]}")
            else:
                print(f"FAILED: {r['error']}")
    finally:
        if restart_daemon:
            subprocess.run(["systemctl", "--user", "start", "hushkeys-daemon"])

    label = args.label or hardware_label(gpu, cpu_only=args.device == "cpu")
    report = {
        "schema": SCHEMA,
        "label": label,
        "date": stamp,
        "hushkeys": hushkeys_commit(),
        "host": host_facts(),
        "gpu": gpu,
        "software": software_facts(venv_python),
        "config": {
            "language": os.environ.get("HUSHKEYS_LANGUAGE", "auto"),
            "model": os.environ.get("HUSHKEYS_MODEL", "medium"),
            "device_forced": args.device or None,
        },
        "audio": {"seconds": seconds, "passage": args.passage if passage_text else None},
        "runs": runs,
        "recommendation": recommend(None if args.device == "cpu" else gpu, runs),
        "note": args.note,
    }
    os.makedirs(REPORTS, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "machine"
    base = os.path.join(REPORTS, f"{slug}-{stamp}")
    json.dump(report, open(base + ".json", "w"), ensure_ascii=False, indent=2)
    open(base + ".md", "w").write(report_markdown(report))
    print(f"\nRecommended: model={report['recommendation']['model']} ({report['recommendation']['reason']})")
    print(f"Report: {base}.md — fill in the checklist, then `hushkeys reports` and commit reports/.")
    summarize()


# ─── Reports ─────────────────────────────────────────────────────────────────

CHECKLIST = """
## Checks by hand

Tick what was tried on this machine; strike what failed and say how.

- [ ] `hushkeys toggle` from a GNOME shortcut pastes into a GTK text field
- [ ] pastes into a terminal with `HUSHKEYS_PASTE_KEYS=ctrl+shift+v`
- [ ] pastes into Firefox or Chromium
- [ ] the dictation state shows in the top bar (or falls back to notifications)
- [ ] the vocabulary file is picked up without restarting the daemon
- [ ] dictation still works after a suspend / resume
- [ ] a dictation over two minutes completes
"""


def report_markdown(r):
    h, g = r["host"], r["gpu"]
    lines = [f"# {r['label']} — {r['date']}", ""]
    lines += [
        f"- **OS**: {h['os']}, kernel {h['kernel']}",
        f"- **Desktop**: {h['desktop']} on {h['session']}, {h['gnome_shell'] or 'no gnome-shell'}",
        f"- **CPU / RAM**: {h['cpu']}, {h['ram_gib']} GiB",
        f"- **GPU**: {g['name']} ({g['vram_mib']} MiB, driver {g['driver']})" if g else "- **GPU**: none",
        f"- **Software**: python {r['software'].get('python')}, faster-whisper {r['software'].get('faster_whisper')},"
        f" ctranslate2 {r['software'].get('ctranslate2')}, CUDA wheels {r['software'].get('cuda_wheels')}",
        f"- **hushkeys**: {r['hushkeys']}, language={r['config']['language']}"
        + (", device forced to CPU" if r["config"]["device_forced"] == "cpu" else ""),
        f"- **Audio**: {r['audio']['seconds']} s, passage `{r['audio']['passage']}`",
    ]
    if r.get("note"):
        lines.append(f"- **Note**: {r['note']}")
    lines += ["", "| Model | Device | Load | Transcription | RTF | Peak VRAM | Agreement | Result |",
              "|---|---|---|---|---|---|---|---|"]
    for x in r["runs"]:
        if x["status"] == "ok":
            lines.append(f"| {x['model']} | {x['device']}/{x['compute']} | {x['load_s']} s | {x['transcribe_s']} s"
                         f" | {x['rtf']} | {x.get('vram_peak_mib') or '—'} | {x.get('agreement')} | OK |")
        else:
            lines.append(f"| {x['model']} | {x.get('device', '?')}/{x.get('compute', '?')} | — | — | — | — | — |"
                         f" **failed**: {x['error']} |")
    rec = r["recommendation"]
    lines += ["", f"**Recommended**: `model={rec['model']}` — {rec['reason']}.", ""]
    lines += ["## Transcriptions", ""]
    for x in r["runs"]:
        if x["status"] == "ok":
            lines += [f"**{x['model']}**", "", f"> {x['text']}", ""]
    lines.append(CHECKLIST.strip())
    return "\n".join(lines) + "\n"


def summarize():
    """reports/README.md: one line per run, across every machine reported."""
    files = sorted(f for f in os.listdir(REPORTS) if f.endswith(".json")) if os.path.isdir(REPORTS) else []
    rows = []
    for f in files:
        r = json.load(open(os.path.join(REPORTS, f)))
        h, g = r["host"], r["gpu"]
        gpu = f"{g['name']} {g['vram_mib']} MiB" if g else "none"
        for x in r["runs"]:
            cell = (f"{x['load_s']} s | {x['transcribe_s']} s | {x['rtf']} | {x.get('vram_peak_mib') or '—'} | {x.get('agreement')}"
                    if x["status"] == "ok" else f"— | — | — | — | failed: {x['error'][:60]}")
            rows.append(f"| [{r['label']}]({f[:-5]}.md) | {h['os']} | {h['session']} | {gpu} | {x['model']} "
                        f"| {x.get('device', '?')}/{x.get('compute', '?')} | {cell} |")
    head = [
        "# Reports",
        "",
        "One report per machine and date, produced by `hushkeys bench` — see the",
        "README, section *Validating on another machine*. This table is regenerated",
        "by `hushkeys reports`; edit the per-machine `.md` files, not this one.",
        "",
        "| Machine | OS | Session | GPU | Model | Device | Load | Transcription | RTF | Peak VRAM | Agreement |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    os.makedirs(REPORTS, exist_ok=True)
    open(os.path.join(REPORTS, "README.md"), "w").write("\n".join(head + rows) + "\n")
    print(f"reports/README.md: {len(rows)} runs over {len(files)} reports.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--run-one":
        _, _, model_id, wav, passage_path = sys.argv
        passage = open(passage_path).read().strip() if passage_path else None
        run_one(model_id, wav, passage)
        return
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    b = sub.add_parser("bench")
    b.add_argument("--wav", help="measure on this recording instead of recording one")
    b.add_argument("--models", default=DEFAULT_MODELS, help=f"comma-separated (default {DEFAULT_MODELS})")
    b.add_argument("--passage", default=os.environ.get("HUSHKEYS_LANGUAGE", "auto"),
                   help="which bench/passage.<lang>.txt to read (default: the configured language, else fr)")
    b.add_argument("--device", choices=["cpu"], help="force the CPU, to measure the no-GPU path")
    b.add_argument("--note", default="", help="free text kept in the report")
    b.add_argument("--label", help="name the report is filed under (default: the card, or the CPU)")
    sub.add_parser("reports")
    args = p.parse_args()
    if args.cmd == "reports":
        summarize()
    elif args.cmd == "bench":
        if args.passage == "auto" or not os.path.exists(os.path.join(REPO, "bench", f"passage.{args.passage}.txt")):
            args.passage = "fr"
        bench(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
