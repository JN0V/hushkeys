<p align="center"><img src="assets/logo.svg" width="128" alt="hushkeys"></p>

# hushkeys — offline dictation

Dictate into **any field** on the desktop, without a single sound leaving the
machine. Microphone recording, transcription by [faster-whisper][fw] on an
NVIDIA GPU, then insertion of the text into the focused window — through the
clipboard, not by simulated typing
([why](#the-text-is-pasted-not-typed)).

[fw]: https://github.com/SYSTRAN/faster-whisper

Built for one situation the polished tools handle badly: GNOME under Wayland, a
laptop with a small NVIDIA card, and technical vocabulary
([why another one](#why-another-one)).

```
hushkeys toggle     # starts, then stops and pastes the text
```

A daemon keeps the model resident in VRAM: a 20 s dictation is transcribed in
6 s, with no reload.

Any language Whisper knows — about a hundred — is dictated the same way. The
language is chosen at install time and kept in `~/.config/hushkeys/config`;
`auto`, the default, detects it on each dictation
([how](#one-language-per-dictation-chosen-per-machine)). The measurements
quoted below were all taken on French speech, the author's.

---

## Why another one

There are a dozen offline dictation tools for Linux in 2026, and the largest —
[Handy][handy], [OpenWhispr][ow], [Speech Note][sn] — are polished, free, and
run on three desktops. hushkeys exists because two earlier ones were tried
first and neither fit, and because what did not fit is not the kind of thing a
bigger project grows out of.

### Where it comes from

[nerd-dictation][nd] gave the shape: two commands behind a keyboard shortcut,
no window, the text lands wherever the cursor is. Its architecture is the
opposite of this one on the two points that matter here — it recognises with
VOSK, in a stream, where Whisper is in another league on technical speech, and
it inserts the text by simulated typing, which is precisely what GNOME under
Wayland does not serve ([why](#ydotool-not-xdotool)).

[Speed of Sound][sos] was the Flathub answer, and it was tested seriously
enough to find its three limits: no GPU, ever; a recogniser three times slower
at equal model size; a vocabulary that only reaches the correction stage, never
the recognition ([details](#why-not-speed-of-sound)).

Neither problem is a missing feature. They are choices, made early, that the
projects are built on. Hence a new one.

### What it does differently

- **GNOME Wayland works, by construction.** The 2026 guides agree that no
  dictation tool is fully reliable under Wayland, and that the usual answer is
  ydotool or wtype "with caveats". Here the caveats were the starting point: the
  text goes through the clipboard and one forged key event, and the README says
  why nothing else can work on Mutter ([why](#the-text-is-pasted-not-typed)).
- **A 2 GB GPU is enough, and the numbers are in the README.** The mainstream
  runs whisper.cpp on the CPU or assumes a real card. Here `medium` sits
  resident in 970 MiB, a 20 s dictation takes 6 s, and the decoding is tuned so
  that an hour of speech peaks where two minutes do
  ([why](#decoding-is-tuned-for-vram-not-for-the-last-percent-of-accuracy)).
- **The vocabulary acts on recognition, not after it.** Most tools fix the text
  once it is out, with a replacement list or a language model. Here the terms
  bias the decoder itself, and the file is re-read at every dictation
  ([why](#technical-vocabulary-goes-through-hotwords)).
- **Nothing sits between the voice and the text.** No cleanup model, no account,
  no cloud option, no application framework: about a thousand lines of bash and
  Python, installed as symlinks, updated by `git pull`, readable in an evening.
  What is pasted is what was said.
- **A failure is never silence.** A failed transcription is reported as such,
  the recording is kept, and a lost CUDA context restarts the daemon
  ([why](#a-failure-is-never-reported-as-silence)).

### What it does not do

- Run anywhere but GNOME on Linux, or shine without an NVIDIA card — the CPU
  fallback works, about half again slower now that the pieces are sized for it.
- Install without the `input` group, a reboot, and one `sudo` for the
  after-suspend fix.
- Offer a window, a settings screen, voice commands, push-to-talk, or a model
  that cleans the text up.
- Come with more than one author. Measurements now come from two machines, one
  of them without a card ([reports/](reports/)).

[handy]: https://github.com/cjpais/handy
[ow]: https://github.com/OpenWhispr/openwhispr
[sn]: https://github.com/mkiol/dsnote
[sos]: https://flathub.org/apps/io.speedofsound.SpeedOfSound

---

## Installation

```bash
sudo apt install -y pulseaudio-utils socat libnotify-bin ydotool wl-clipboard
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv is not already there
sudo usermod -aG input $USER     # access to /dev/uinput — reboot required
./install.sh
```

On **Ubuntu 22.04** that first line is not enough, and `install.sh` says so by
stopping at step 1. Jammy's `ydotool` is 0.1.8, which ships the client alone —
no `ydotoold`, no systemd unit, both of which arrived in 1.0. Build it, and add
the udev rule that jammy does not ship either, without which `ydotoold` is
refused `/dev/uinput` even once you are in the `input` group:

```bash
git clone --depth 1 --branch v1.0.4 https://github.com/ReimuNotMoe/ydotool
cmake -B build -DCMAKE_INSTALL_PREFIX=/usr/local ydotool && cmake --build build
sudo install -m755 build/ydotool build/ydotoold /usr/local/bin/
sudo install -Dm644 build/ydotool.service /usr/local/lib/systemd/user/ydotool.service
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee /etc/udev/rules.d/60-uinput.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Without the rule the daemon transcribes and nothing is ever pasted — the one
failure the design goes out of its way to make visible, since `type_text`
leaves the text in the clipboard and says why.

`install.sh` builds the venv, creates the symlinks and enables the services; it
writes nothing outside `~/bin`, `~/.config` and `~/.local/share`. It asks one
question, the dictation language — a Whisper code such as `fr`, `en`, `de`, or
`auto` — and answers it from `HUSHKEYS_LANGUAGE` when there is no terminal. It
leaves three things to do by hand, and reminds you of them on the way out:

- **Reboot**, for the `input` group — logging back in is not enough
  ([why](#ydotool-not-xdotool)). The reboot also settles `PATH`: Ubuntu only adds
  `~/bin` if it exists when the session opens.
- A GNOME **keyboard shortcut** (*Settings > Keyboard > Custom Shortcuts*) on
  `~/bin/hushkeys toggle`. It can be set from `gsettings` instead, with one
  catch: fill `name`, `command` and `binding` **first**, then write the
  `custom-keybindings` list. Appending the path before the keys are set leaves
  gnome-settings-daemon holding an empty entry — Settings displays the shortcut
  correctly and it never fires.
- On a laptop, the **CUDA-after-suspend fix** — a symlink in
  `/etc/systemd/system`, the only step that needs `sudo`
  ([why](#running-it)).

To dictate into a terminal, add a second shortcut — terminals paste with
`Ctrl+Shift+V`:

```
env HUSHKEYS_PASTE_KEYS=ctrl+shift+v /home/<user>/bin/hushkeys toggle
```

Personal configuration lives in `~/.config/hushkeys/`, outside the repository:
`config` holds the language and, optionally, the model; `vocabulary.txt` the
terms the decoder should know.

The dictation state shows as an icon in the top bar
([why](#the-state-lives-in-the-top-bar-not-in-a-banner)); it needs `python3-gi`,
which Ubuntu ships, and under GNOME the AppIndicator extension, enabled by
default on Ubuntu. Without them the state falls back to notifications.

Without an NVIDIA GPU everything still works: the daemon falls back to CPU
`int8` and cuts the recording into longer pieces, which is most of what the
card was buying ([why](#the-piece-is-sized-for-the-device)). On an i5-8250U
with no card, `medium` transcribes 25.5 s of French in 11.1 s — see
[reports/](reports/).

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
real French speech, VAD on:

| Configuration | Load | Transcription | VRAM |
|---|---|---|---|
| **medium / GPU / int8** | 3.3 s | **6.2 s** | 970 MiB |
| medium / CPU / int8 | 4.3 s | 12.7 – 18.1 s | — |
| small / GPU / int8 | 2.7 s | 2.6 s | 362 MiB |
| medium / GPU / float16 | — | unsupported (CC 6.1) | — |

`medium` in int8 fits in **970 MiB**, less than half of the 2 GB.
`large-v3` weighs about 1.6 GB and would leave no headroom: it stays reserved for
better-equipped machines, through `model=large-v3` in `~/.config/hushkeys/config`
(or `HUSHKEYS_MODEL=large-v3` in the environment, which wins over the file).

The 12.7 → 18.1 s spread between two identical CPU runs is thermal throttling on
the 15 W part. Any CPU measurement on this chassis should be read as a range,
never as a point.

That 970 MiB is the resident model, not the peak. What the ">= 4 GB of VRAM"
advice is really about is the decoding headroom on top of it — see below.

### Decoding is tuned for VRAM, not for the last percent of accuracy

Whisper decodes 30-second windows. With `condition_on_previous_text` (the
faster-whisper default), each window is prompted with the previous ~220 tokens,
and the cross-attention of those tokens against the 1500 encoder frames, times
the beam width, is allocated on the GPU. It is that product — not the length of
the recording as such — that grows: the first window is prompted with 3 tokens,
the second with ~100, the third with ~220, and on a 2 GB card the third one is
where the allocation fails.

Measured on the MX230, medium/int8, real French speech, peak VRAM over the whole
transcription:

| Configuration | 2 min | 10 min | 1 h |
|---|---|---|---|
| `beam_size=5`, conditioning on | 1964 MiB → **OOM** | — | — |
| `beam_size=5`, conditioning off | 1484 MiB | 1548 MiB → **OOM** | — |
| `beam_size=1`, conditioning off | 1292 MiB | 1292 MiB | 1292 MiB |
| **`beam_size=2`, conditioning off** | — | **1324 MiB** | **1356 MiB** |

Once the conditioning is dropped, the peak stops depending on the length of the
dictation at all: an hour of speech costs the same VRAM as two minutes. That is
the property worth having on a small card — the alternative is a dictation
length beyond which everything fails, which is exactly the bug this replaced.

Those figures were measured without the vocabulary, though, and the vocabulary
turned out to reopen the same door — see [below](#the-vocabulary-reopened-the-same-door).

The accuracy cost is small on dictation, where each 30-second window is largely
self-contained; dropping the conditioning also removes the repetition loops it
can trigger. Over 60 s of read French, `beam_size=2` without conditioning agrees
with the old `beam_size=5` + conditioning on **98.6 %** of words, the only
difference being a sentence break. `beam_size=1` saves a further 32 MiB but
starts dropping proper nouns at window boundaries — it transcribed "Roosevelt"
as "Bruce Wells" — which is exactly the context the conditioning used to supply.
Two beams buy that back cheaply.

Transcription runs at roughly **0.3 × real time**, so a 3-minute dictation takes
about a minute.

### The vocabulary reopened the same door

Turning off `condition_on_previous_text` caps the decoder prompt, and the
measurements above say it works. It does — until something else prompts the
decoder. `hotwords` does, through the very same branch:

```python
# faster_whisper/transcribe.py
if previous_tokens or (hotwords and not prefix):
    prompt.append(tokenizer.sot_prev)
```

Since the daemon passes the vocabulary on every call, the cap was off in
production and nowhere else. Same 38 s recording, same options, the vocabulary
the only variable:

| | Result |
|---|---|
| without the vocabulary | 11.6 s, 8 segments |
| with the 17 terms | **OOM** on the 2nd window |

Reproducible in both directions. The dependency on length is real but indirect —
the failure always lands on the *second* 30-second window:

| 10 s | 20 s | 38 s |
|---|---|---|
| OK | OK | **OOM** |

And it is not a budget of prompt tokens: **4 terms fail where 12 pass**,
deterministically across runs, because the prompt changes *which* tokens get
decoded and not merely how many. Trimming the vocabulary is a coin toss, not a
fix.

Cutting is. The recording is split into pieces of at most 20 s and each piece
gets its own `transcribe()` call, which bounds what any single call has to
decode.

The cut lands on a silence rather than on a stopwatch: `max_speech_duration_s`
splits at the last silence over 100 ms, where cutting at a flat interval slices
through a word and both halves then decode into something neither of them was.

The 20 s is not "just under the 30-second window", and assuming it was is what
made the first attempt at this fix fail on the first real long dictation. The
silences are dropped, so a piece is 20 s of *dense* speech — worth far more
decoded tokens than any natural 30 s of dictation, pauses included. It is those
tokens that are allocated, so the piece has to be short enough that a full one
still fits. On 164 s of real French dictation, with the vocabulary:

| `CHUNK_SECONDS` | Peak VRAM | |
|---|---|---|
| 28 | 1484 MiB | **OOM** |
| 24 | 1356 MiB | OK |
| 22 | 1324 MiB | OK |
| **20** | **1324 MiB** | OK |

20 rather than 24 to sit two steps below the cliff rather than one, and because
the peak has plateaued by then — shorter pieces buy no further margin. The
property that was wanted all along finally holds against real speech:

| | Peak VRAM |
|---|---|
| 164 s, full vocabulary | 1324 MiB |
| 8 min, full vocabulary | **1324 MiB** |

The time cost is nil: 55.5 s at 20 against 53.8 s at 24, on the same 164 s.

### The piece is sized for the device

Everything above is a VRAM argument, and it does not survive the machine having
no VRAM. The encoder runs over a 30-second window whatever the piece holds, so
a 20 s piece pays for a full pass and fills two thirds of it — on the card that
is the price of not falling off the cliff, and on the CPU it buys nothing at
all. On an i5-8250U with no card, `medium` in int8, over 156.6 s of French:

| `CHUNK_SECONDS` | Pieces | Transcription | Peak RSS |
|---|---|---|---|
| 20 | 12 | 114.6 s, 124.3 s | 2173 MiB |
| **30** | **6** | **67.6 s, 71.1 s** | 2173 MiB |

Same 360 words out, same peak, 42 % less time — twelve windows encoded against
six, for the same speech. So the length is chosen per device: 20 s on the card,
one window on the CPU, read off the loaded model rather than off
`pick_compute_type()`, since the daemon falls back to the CPU when the GPU load
fails and the pieces have to follow the model that exists.

Whether a piece spanning several windows would do better is untested. The
sample above repeats one passage six times, so a single 120 s piece holding all
six fails the compression-ratio threshold and decodes twice over — 156.7 s,
worse than either row. That measures the repetition, not the length.

### The fallback nobody looks at

Chunk size alone never explained the failures. 4 vocabulary terms failed where
12 passed; a 20 s piece died where a 25 s one lived. Turning on faster-whisper's
debug log gave the missing line:

```
Compression ratio threshold is not met with temperature 0.0 (27.045455 > 2.400000)
```

A compression ratio of 27 is a repetition loop: the decoder goes round in
circles and fills the window to its 448-token ceiling. faster-whisper then
retries the window by sampling — and `best_of` defaults to **5**, so the retry
decodes 2.5 × wider than the `beam_size=2` it is replacing. Tokens times width,
in one step. That is the discrete jump, and because a loop depends on *what is
being said* rather than on how long it is, it lands unpredictably.

`best_of=2` aligns the fallback with the beam. Every configuration that used to
OOM then completes — including the 28 s pieces that failed reliably before. The
failure was never really about size.

### Context across the cuts

Each piece is decoded cold, so a cut falling mid-sentence leaves the decoder
guessing. On a real 164 s dictation containing 56 s without a single pause, one
seam came back with its clause trailing off into an ellipsis and the next piece
starting cold right after it: the qualifier the speaker had used was gone, and
the sentence read as cut off mid-thought.

So the tail of the running transcription — 40 words — is handed to the next
piece as `initial_prompt`: the context `condition_on_previous_text` used to
give, except bounded, and paid for once rather than growing with the recording.

The obvious alternative, repeating a few seconds of audio at the head of each
piece and stitching the texts, reads better at the seam — and silently drops
speech, the more so the more it repeats:

| | Speech lost against the baseline |
|---|---|
| overlap 3 s | one word |
| overlap 5 s | eight words — a whole enumeration |
| overlap 7 s | more still |
| **40 words of context** | **none** |

Not a stitching bug — the words were never decoded. Measured on the same
recording, the context version drops nothing, restores the missing qualifier,
and fixes a word the baseline had misheard. A ragged seam is cosmetic; a
dictation missing a phrase the speaker said is not.

The context costs 64 MiB — the peak goes from 1324 to **1388 MiB** — and holds
there over 8 minutes of speech. The result is deterministic run to run.

### A failure is never reported as silence

A CUDA out-of-memory used to be caught, logged, and answered with an empty
string — which the client could only show as "Nothing heard", pointing the user
at their microphone instead of at the GPU. Worse, the CUDA context does not
survive it: every later transcription then failed with
`cudaErrorInvalidDevice: invalid device ordinal`, so the *next* dictation was
silently lost too.

So: the daemon answers a failure with a marked line (a leading `\x01`, which
cannot occur in dictated text) and the client reports it as a failure, keeping
the recording in `/tmp/hushkeys-failed.wav`. And because a lost CUDA context is
not recoverable in-process, the daemon exits non-zero on a CUDA error and lets
`Restart=on-failure` reload the model.

### The state lives in the top bar, not in a banner

A dictation goes through three states — listening, transcribing, done — and a
notification for each of them answers the wrong question. A banner disappears
after a few seconds, so it says what *happened*, never where things *stand*:
mid-sentence, nothing on screen tells whether the microphone is still open.
And every dictation left its three entries in the notification list, which
piled up over a day.

So the state is a **top-bar icon** — `bin/hushkeys-indicator`, a
StatusNotifierItem, the protocol behind app indicators. Under GNOME it needs the
AppIndicator extension, enabled by default on Ubuntu; KDE and most other
desktops host it natively. `hushkeys start` spawns it, `stop` drives it over
D-Bus, and it exits on its own once the text is pasted:

| state        | icon               | then                              |
|--------------|--------------------|-----------------------------------|
| listening    | microphone         | until `stop`                      |
| transcribing | hourglass          | until the daemon answers          |
| done         | check mark         | 1.5 s, then the icon goes away    |
| warning      | `⚠` — nothing heard, text left in the clipboard | stays until dismissed |
| error        | `⊗` — transcription failed              | stays until dismissed |

A warning or an error stays put until the next dictation, a click, or *Dismiss*
in its menu: that is the point. A notification still carries the text worth
reading (why it failed, where to look), since the top bar cannot. Without a
StatusNotifier host, or with `HUSHKEYS_INDICATOR=0`, everything goes through
notifications as before.

Those notifications are now **transient**: GNOME drops them once the banner is
gone instead of keeping them in the list. Only failures are kept. The obvious
alternative — one notification updated in place with `--replace-id` — was tried
and withdrawn: updating a notification while its banner is animating out
crashes the message tray of GNOME Shell 50 (`TypeError: this._notification is
null` in `_showNotificationCompleted`), after which no banner shows again until
the session is restarted.

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

`hotwords` is not free on a small card, though: it prompts the decoder, and that
is what the [chunking](#the-vocabulary-reopened-the-same-door) exists to pay
for.

### One language per dictation, chosen per machine

Nothing in the pipeline is tied to a language: Whisper is multilingual, the
vocabulary is a list of terms, and the text is pasted as is. The language is
therefore a setting, not a constant — `language=` in `~/.config/hushkeys/config`,
asked once by `install.sh` — and it is per machine because it is per person.

A fixed code (`fr`, `en`, …) is the safe choice: the decoder never has to guess,
and a two-second recording cannot come back in the wrong language. `auto` is for
people who switch. It asks faster-whisper to detect the language on the **first
piece** of the dictation, which is free — detection runs on the encoder output
the decoding needs anyway — and then pins every later piece to what was found.
Detecting each piece on its own would be cheaper to write and wrong in practice:
a short piece of French can be read as Italian, or as English with the words
translated, and one such piece in the middle of a dictation is worse than a
whole dictation in the wrong language. So it is one language per dictation, any
language across dictations; the daemon logs what it detected.

`HUSHKEYS_LANGUAGE` in the environment overrides the file, for one shortcut
bound to another language.

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
bin/hushkeys-indicator    top-bar icon showing the dictation state
bin/hushkeys-daemon       daemon wrapper for systemd
bin/hushkeys-daemon.py    resident-model daemon (Unix socket)
bin/transcribe.py         fallback without the daemon
bin/hushkeys-bench.py     `hushkeys bench` / `hushkeys reports` — see below
bench/                    passages to read aloud for the bench, one per language
reports/                  one report per machine measured, and their index
systemd/                  user units and the post-suspend CUDA fix
config/                   templates: config (language, model) and vocabulary
assets/                   logo (colour, and single-ink for favicons)
```

## Running it

```bash
hushkeys daemon-status
systemctl --user status hushkeys-daemon
journalctl --user -u hushkeys-daemon -f
```

When a dictation fails, the notification says so instead of claiming nothing was
heard, and the recording is kept — so the failure can be replayed instead of
re-dictated:

```bash
journalctl --user -u hushkeys-daemon -n 20
echo /tmp/hushkeys-failed.wav | socat -t900 - UNIX-CONNECT:/tmp/hushkeys-daemon.sock
```

A CUDA error makes the daemon exit on purpose: the context is lost and no later
transcription would succeed, so `Restart=on-failure` reloads the model. A
restart therefore looks like a failure in `systemctl status` — that is the fix
working, not the bug.

The `nvidia-uvm-reload.service` fix reloads `nvidia_uvm` after a suspend:
without it, CUDA becomes unusable on wake on a laptop, and the daemon silently
falls back to the CPU.

The repository can be cloned anywhere: the systemd unit points at
`~/bin/hushkeys-daemon`, and the wrapper walks back to the repository through
`readlink`. Everything is installed as symlinks, so a `git pull` is enough to
update — there is no need to reinstall. The daemon holds its code in memory
though, so a pull that touches it takes effect on
`systemctl --user restart hushkeys-daemon`.

## Validating on another machine

Most numbers in this README come from one laptop: an MX230 with 2 GB, GNOME 50
under Wayland. The second report is an i5-8250U with no card at all, on Ubuntu
22.04 — it is what showed that the piece length had to depend on the device,
and what the 22.04 prerequisites above were found on. Another Ubuntu, X11
instead of Wayland, or a bigger card each changes something, and the way to
know what is to measure it there and keep the result next to the others.

```bash
hushkeys bench                         # reads bench/passage.<language>.txt aloud
hushkeys bench --models small,medium   # skip large-v3 (a 3 GB download)
hushkeys bench --device cpu            # the no-GPU path, on a machine that has one
hushkeys bench --wav some-recording.wav
```

The bench stops the daemon for the duration (it holds the VRAM), then runs
each model in its own process — an out-of-memory leaves a CUDA context that
cannot be reused, so one failure must not colour the next run — through the
daemon's own `transcribe_file`: same chunking, same decoding options, same
vocabulary, so the timings are those of a real dictation. For each model it
records load time, transcription time, real-time factor, the peak of the whole
card's VRAM, and the share of words agreeing with the passage. It ends with a
recommendation for `model=` in `~/.config/hushkeys/config`: on a GPU, the
largest model whose peak stays under 80 % of the card, the rest being the
headroom a dictation longer than the passage needs; without one, the largest
that keeps up with speech.

It writes `reports/<label>-<date>.json` and `.md`, the label naming the card
(`mx230`, `rtx-2070`) or the CPU when there is none (`cpu-i7-10510u`) — never
the machine: a hostname has no business in a public repository, and the report
records none. `--label` overrides it. The `.md` carries a checklist
for what a bench cannot measure — pasting into a GTK field, a terminal and a
browser, the top-bar indicator, dictation after a suspend, a dictation over two
minutes — to be ticked by hand, with a word on how anything failed. Then
`hushkeys reports` rebuilds `reports/README.md`, the table across all machines,
and both go into a commit. The recording itself stays out of the repository,
under `~/.local/share/hushkeys/bench/`.

Two things a report will show that are worth knowing beforehand. Forcing the
wrong language is expensive, not merely wrong: the English clip used to check
the bench took 48 s under `language=fr` and 17 s under `en`, with the VRAM
peak at the very top of the card, because every window fails the quality
thresholds and gets decoded again. And `HUSHKEYS_DEVICE=cpu` is honoured by the
daemon too, so a machine with a card can run the CPU path for comparison
without touching its config.

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
