# hushkeys — offline French dictation

Dictate into **any field** on the desktop, without a single sound leaving the
machine. Microphone recording, transcription by [faster-whisper][fw] on an
NVIDIA GPU, then insertion of the text into the focused window — through the
clipboard, not by simulated typing
([why](#the-text-is-pasted-not-typed)).

[fw]: https://github.com/SYSTRAN/faster-whisper

```
hushkeys toggle     # starts, then stops and pastes the text
```

A daemon keeps the model resident in VRAM: a 20 s dictation is transcribed in
6 s, with no reload.

The recogniser is pinned to French (`language="fr"` in `bin/hushkeys-daemon.py`)
and the shipped vocabulary is French-flavoured, but nothing else in the design is
language-specific — one constant changes that.

---

## Installation

```bash
sudo apt install -y pulseaudio-utils socat libnotify-bin ydotool wl-clipboard
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv is not already there
sudo usermod -aG input $USER     # access to /dev/uinput — reboot required
./install.sh
```

`install.sh` builds the venv, creates the symlinks and enables the services; it
writes nothing outside `~/bin`, `~/.config` and `~/.local/share`. It leaves three
things to do by hand, and reminds you of them on the way out:

- **Reboot**, for the `input` group — logging back in is not enough
  ([why](#ydotool-not-xdotool)). The reboot also settles `PATH`: Ubuntu only adds
  `~/bin` if it exists when the session opens.
- A GNOME **keyboard shortcut** (*Settings > Keyboard > Custom Shortcuts*) on
  `~/bin/hushkeys toggle`.
- On a laptop, the **CUDA-after-suspend fix** — a symlink in
  `/etc/systemd/system`, the only step that needs `sudo`
  ([why](#running-it)).

To dictate into a terminal, add a second shortcut — terminals paste with
`Ctrl+Shift+V`:

```
env HUSHKEYS_PASTE_KEYS=ctrl+shift+v /home/<user>/bin/hushkeys toggle
```

Personal configuration lives in `~/.config/hushkeys/vocabulary.txt`, outside the
repository.

Without an NVIDIA GPU everything still works: the daemon falls back to CPU
`int8`, two to three times slower (see the table below).

---

## The decisions, and why

### ydotool, not xdotool

`xdotool` only works under X11. Under Wayland, **Mutter does not implement
`zwp_virtual_keyboard_v1`**: that is a wlroots protocol, and GNOME deliberately
chose not to follow it. Any typing tool that goes through the compositor
therefore fails silently on GNOME.

`ydotool` works around the problem from below: it writes to `/dev/uinput`, the
kernel forges the events, and libinput delivers them as if they came from a real
keyboard. No Wayland protocol is involved.

Practical consequences:

- The user must be in the `input` group. The package's
  `/usr/lib/udev/rules.d/80-uinput.rules` already sets
  `GROUP="input", MODE="0660"` on `/dev/uinput` — nothing to write yourself.
- The package ships a **user** service, `ydotool.service`, which runs
  `ydotoold`. No root daemon needed.
- Group membership requires a **reboot**, not merely a fresh login.
  `systemd --user` survives the end of a session as long as one user process
  remains, and its supplementary groups are frozen when it itself starts: every
  `--user` service it restarts afterwards — `ydotoold` included — inherits the
  old set of groups. Logging out and back in changes nothing. Since Ubuntu 26.04
  ships neither `sg` nor `newgrp`, there is no hot fix either.

  To check: `loginctl list-sessions` shows a `manager` entry whose leader is the
  `systemd --user`, with its original start time.

If `ydotoold` is not running, `hushkeys` leaves the text in the clipboard rather
than losing the transcription.

### The text is pasted, not typed

`ydotool type` is unusable on a non-US keyboard. It emits raw keycodes and
assumes a US layout — its own `--help` concedes as much:

> Since there's no way to know how many keyboard layouts are there in the world,
> we're using raw keycodes now.

On AZERTY, `KEY_A` produces a `q`: the dictation comes out as gibberish.

So the text travels through the clipboard, and only a paste shortcut is emitted.
`Ctrl`, `Shift` and `V` occupy the same physical position on AZERTY and QWERTY,
so their raw keycodes are correct on either layout. Welcome side effect:
insertion is instant, where character-by-character typing took several seconds
on a paragraph.

The combination is configurable (`HUSHKEYS_PASTE_KEYS`) because it depends on the
target application. It cannot be chosen automatically — GNOME denies
`org.gnome.Shell.Introspect.GetWindows` to unauthorised callers, so there is no
way to know which window has focus.

In practice `Ctrl+V` is enough nearly everywhere, including at a shell prompt:
Ptyxis does bind pasting to `Ctrl+Shift+V` only
(`org.gnome.Ptyxis.Shortcuts paste-clipboard`), but **fish binds `Ctrl+V` to
`fish_clipboard_paste`** in its presets (`__fish_shared_key_bindings.fish`), so
the paste lands anyway. The second `Ctrl+Shift+V` shortcut stays useful for
full-screen programs that grab `Ctrl+V` for themselves — `vim` in insert mode,
typically — and for shells that treat `Ctrl+V` as `quoted-insert`
(bash/readline).

The clipboard is restored after pasting, but only if it held text: non-text
content present before the dictation is lost.

The alternatives were ruled out: `wtype` does not work under GNOME (same missing
wlroots protocol), and `dotool`, which does handle layouts, is not packaged in
Ubuntu.

### A dedicated CPython 3.12, not the system Python

Ubuntu 26.04 only ships **Python 3.14**, for which `ctranslate2` does not publish
wheels yet. `uv` installs a standalone CPython 3.12 under `~/.local`, without
privileges and without touching the system Python.

That is also why no Python version appears hardcoded anywhere: not in the
scripts, not in the systemd unit. `bin/hushkeys-env.sh` resolves the paths by
glob, and `bin/hushkeys-daemon` acts as the service's wrapper. The venv can be
rebuilt against another version without editing a thing.

### The compute type is queried, not guessed

`float16` requires a **compute capability >= 7.0**. On Pascal — MX230, CC 6.1 —
ctranslate2 refuses outright:

```
ValueError: Requested float16 compute type, but the target device or backend
do not support efficient float16 computation.
```

`int8`, on the other hand, works very well there, through the dp4a instructions.

Rather than hardcoding that choice or relying on an exception,
`pick_compute_type()` queries
`ctranslate2.get_supported_compute_types("cuda")`. The same checkout thus runs
in `int8` on an MX230 and in `float16` on an RTX 2070, unmodified.

### medium by default, not large-v3

Measured on this machine — i7-10510U (4 c / 15 W) + MX230 2 GB — over 20.0 s of
real French speech, `beam_size=5`, VAD on:

| Configuration | Load | Transcription | VRAM |
|---|---|---|---|
| **medium / GPU / int8** | 3.3 s | **6.2 s** | 970 MiB |
| medium / CPU / int8 | 4.3 s | 12.7 – 18.1 s | — |
| small / GPU / int8 | 2.7 s | 2.6 s | 362 MiB |
| medium / GPU / float16 | — | unsupported (CC 6.1) | — |

`medium` in int8 fits in **970 MiB**, less than half of the 2 GB — the
">= 4 GB of VRAM" advice you read everywhere is very pessimistic for this model.
`large-v3` weighs about 1.6 GB and would leave no headroom: it stays reserved for
better-equipped machines, through `HUSHKEYS_MODEL=large-v3`.

The 12.7 → 18.1 s spread between two identical CPU runs is thermal throttling on
the 15 W part. Any CPU measurement on this chassis should be read as a range,
never as a point.

### Technical vocabulary goes through `hotwords`

Unbiased, on a real dictated sentence:

> …on va faire du GitOps en utilisant **Bligacé** et Ansible

With the vocabulary declared:

> …on va faire du GitOps en utilisant **IaC** et Ansible

`git worktree`, mangled into "Gitworktree" without bias, comes through as well.
Measured cost: **+0.2 s** (6.3 → 6.5 s). `initial_prompt` gives exactly the same
result; `hotwords` wins because it feeds directly from a list of terms.

The file is **re-read on every transcription**: adding a term takes effect
immediately, without restarting the daemon or reloading the model.

### Why not Speed of Sound

The Flathub application `io.speedofsound.SpeedOfSound` covers the same need and
types through the XDG portals, without ydotool and without the `input` group —
which is cleaner on that point. Three limits ruled it out:

- **No GPU, at all.** `PROVIDER` is `"cpu"`, hardcoded in the bytecode, and the
  Flathub runtime carries no CUDA (Speech Note had to publish a separate NVIDIA
  add-on for that).
- **sherpa-onnx is roughly 3× slower than CTranslate2** at equal model size on
  this CPU: `small` took 18 to 23 s there, where faster-whisper transcribes
  `medium` in 12 to 18 s.
- **`custom-vocabulary` does not act on recognition.** It is only read by the
  LLM correction stage; there is no acoustic biasing path in the application.
  sherpa-onnx's Whisper and Canary recognisers do not support hotwords anyway —
  those are reserved for transducer models.

### Recording format

`parecord --channels=1 --rate=16000 --format=s16le`: exactly what Whisper expects
as input, so no intermediate resampling.

---

## Layout

```
install.sh                venv, symlinks, services — idempotent
bin/hushkeys-env.sh       shared environment (venv, CUDA, model, vocabulary)
bin/hushkeys              front end: start / stop / toggle / daemon-*
bin/hushkeys-daemon       daemon wrapper for systemd
bin/hushkeys-daemon.py    resident-model daemon (Unix socket)
bin/transcribe.py         fallback without the daemon
systemd/                  user units and the post-suspend CUDA fix
config/                   vocabulary template (never the real configuration)
```

## Running it

```bash
hushkeys daemon-status
systemctl --user status hushkeys-daemon
journalctl --user -u hushkeys-daemon -f
```

The `nvidia-uvm-reload.service` fix reloads `nvidia_uvm` after a suspend:
without it, CUDA becomes unusable on wake on a laptop, and the daemon silently
falls back to the CPU.

The repository can be cloned anywhere: the systemd unit points at
`~/bin/hushkeys-daemon`, and the wrapper walks back to the repository through
`readlink`. Everything is installed as symlinks, so a `git pull` is enough to
update — there is no need to reinstall.

## Coming from `dictation`

The project used to be called `dictation`, with a `dictee` command. Re-running
`./install.sh` performs the migration: it copies the vocabulary over to
`~/.config/hushkeys/`, stops and disables `dictation-daemon.service`, and removes
the `~/bin/dictee` and `~/bin/dictation-daemon` symlinks.

Two things it deliberately does not touch:

- The GNOME **keyboard shortcut**, which still points at `~/bin/dictee` — repoint
  it at `~/bin/hushkeys toggle` by hand.
- The old `~/.local/share/dictation-venv` and `~/.config/dictation`, several GB
  between them, which it only reports. Delete them once hushkeys works.

Environment variables lost their `DICTATION_` prefix along the way:
`HUSHKEYS_MODEL`, `HUSHKEYS_PASTE_KEYS`, `HUSHKEYS_VENV`,
`HUSHKEYS_CONFIG_DIR`.

---

## Licence

MIT — see [LICENSE](LICENSE).

The code derives from no existing project. In particular it takes nothing from
[nerd-dictation][nd] (GPL-3.0), which addresses the same need with an entirely
different architecture: Python, VOSK streaming, a single script.

`ydotool` is AGPL-3.0, but it is invoked as a separate process, without linking
or code integration — a command-line invocation stays at arm's length and does
not extend its licence to the caller. Same for `parecord`, `socat` and
`notify-send`. The Python dependencies — faster-whisper and CTranslate2 — are
MIT.

[nd]: https://github.com/ideasman42/nerd-dictation
