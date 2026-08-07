# dictation — dictée vocale française hors-ligne

Dicter dans **n'importe quel champ** du bureau, sans qu'aucun son ne quitte la
machine. Enregistrement micro, transcription par [faster-whisper][fw] sur GPU
NVIDIA, puis frappe du texte dans la fenêtre active.

[fw]: https://github.com/SYSTRAN/faster-whisper

```
dictee toggle     # démarre, puis arrête et tape le texte
```

Un daemon garde le modèle résident en VRAM : une dictée de 20 s est transcrite
en 6 s, sans rechargement.

---

## Installation

```bash
sudo apt install -y pulseaudio-utils socat libnotify-bin ydotool
sudo usermod -aG input $USER     # accès à /dev/uinput — déconnexion requise
./install.sh
```

Puis un raccourci clavier GNOME (*Settings > Keyboard > Custom Shortcuts*) sur
`~/bin/dictee toggle`.

Configuration personnelle dans `~/.config/dictation/vocabulary.txt` — hors dépôt.

---

## Les décisions, et pourquoi

### ydotool, et pas xdotool

`xdotool` ne fonctionne que sous X11. Sous Wayland, **Mutter n'implémente pas
`zwp_virtual_keyboard_v1`** : c'est un protocole wlroots, et GNOME a
délibérément choisi de ne pas le suivre. Tout outil de frappe passant par le
compositeur échoue donc silencieusement sur GNOME.

`ydotool` contourne le problème par le bas : il écrit dans `/dev/uinput`, le
noyau forge les événements, et libinput les distribue comme s'ils venaient d'un
vrai clavier. Aucun protocole Wayland n'est impliqué.

Conséquences pratiques :

- L'utilisateur doit être dans le groupe `input`. La règle
  `/usr/lib/udev/rules.d/80-uinput.rules` du paquet pose déjà
  `GROUP="input", MODE="0660"` sur `/dev/uinput` — rien à écrire soi-même.
- Le paquet fournit un service **utilisateur** `ydotool.service` qui lance
  `ydotoold`. Pas besoin de daemon root.
- L'appartenance au groupe ne prend effet **qu'après reconnexion**.

Si `ydotoold` ne tourne pas, `dictee` bascule sur le presse-papiers plutôt que
de perdre la transcription.

### Un CPython 3.12 dédié, pas le Python système

Ubuntu 26.04 ne livre que **Python 3.14**, pour lequel `ctranslate2` ne publie
pas encore de wheels. `uv` installe un CPython 3.12 autonome dans `~/.local`,
sans privilèges et sans toucher au Python système.

C'est aussi pour cela qu'aucune version de Python n'apparaît en dur : ni dans
les scripts, ni dans le service systemd. `bin/dictation-env.sh` résout les
chemins par glob, et `bin/dictation-daemon` sert d'enveloppe au service. On
peut reconstruire le venv avec une autre version sans rien éditer.

### Le type de calcul est interrogé, pas deviné

`float16` exige une **compute capability >= 7.0**. Sur Pascal — MX230, CC 6.1 —
ctranslate2 refuse explicitement :

```
ValueError: Requested float16 compute type, but the target device or backend
do not support efficient float16 computation.
```

`int8` y fonctionne en revanche très bien, via les instructions dp4a.

Plutôt que de coder ce choix en dur ou de s'en remettre à une exception,
`pick_compute_type()` interroge `ctranslate2.get_supported_compute_types("cuda")`.
Le même dépôt tourne ainsi en `int8` sur MX230 et en `float16` sur RTX 2070,
sans modification.

### medium par défaut, pas large-v3

Mesuré sur ce poste — i7-10510U (4 c / 15 W) + MX230 2 Go — sur 20,0 s de parole
française réelle, `beam_size=5`, VAD actif :

| Configuration | Chargement | Transcription | VRAM |
|---|---|---|---|
| **medium / GPU / int8** | 3,3 s | **6,2 s** | 970 Mio |
| medium / CPU / int8 | 4,3 s | 12,7 – 18,1 s | — |
| small / GPU / int8 | 2,7 s | 2,6 s | 362 Mio |
| medium / GPU / float16 | — | non supporté (CC 6.1) | — |

`medium` en int8 tient dans **970 Mio**, soit moins de la moitié des 2 Go —
la consigne « >= 4 Go de VRAM » qu'on lit partout est très pessimiste pour ce
modèle. `large-v3` pèse environ 1,6 Go et ne laisserait pas de marge : il reste
réservé aux machines mieux dotées, via `DICTATION_MODEL=large-v3`.

L'écart 12,7 → 18,1 s entre deux passes CPU identiques, c'est le throttling
thermique du 15 W. Toute mesure CPU sur ce châssis est à prendre comme une
fourchette, jamais comme un point.

### Le vocabulaire technique passe par `hotwords`

Sans biais, sur une phrase de dictée réelle :

> …on va faire du GitOps en utilisant **Bligacé** et Ansible

Avec le vocabulaire déclaré :

> …on va faire du GitOps en utilisant **IaC** et Ansible

`git worktree`, mal découpé en « Gitworktree » sans biais, passe également.
Coût mesuré : **+0,2 s** (6,3 → 6,5 s). `initial_prompt` donne exactement le
même résultat ; `hotwords` est retenu parce qu'il s'alimente directement depuis
une liste de termes.

Le fichier est **relu à chaque transcription** : ajouter un terme prend effet
immédiatement, sans redémarrer le daemon ni recharger le modèle.

### Pourquoi pas Speed of Sound

L'application Flathub `io.speedofsound.SpeedOfSound` couvre le même besoin et
tape via les portails XDG, sans ydotool ni groupe `input` — c'est plus propre
sur ce point. Trois limites l'ont écartée :

- **Aucun GPU possible.** `PROVIDER` vaut `"cpu"`, en dur dans le bytecode, et
  le runtime Flathub n'embarque pas CUDA (Speech Note a dû publier un addon
  NVIDIA distinct pour cela).
- **sherpa-onnx est environ 3× plus lent que CTranslate2** à modèle égal sur ce
  CPU : `small` y demandait 18 à 23 s là où faster-whisper transcrit `medium`
  en 12 à 18 s.
- **`custom-vocabulary` n'agit pas sur la reconnaissance.** Il n'est lu que par
  l'étape de correction par LLM ; il n'existe aucun chemin de biasing acoustique
  dans l'application. Les recognizers Whisper et Canary de sherpa-onnx ne
  supportent d'ailleurs pas les hotwords — réservés aux modèles transducer.

### Format d'enregistrement

`parecord --channels=1 --rate=16000 --format=s16le` : c'est exactement ce
qu'attend Whisper en entrée, donc aucun rééchantillonnage intermédiaire.

---

## Structure

```
bin/dictation-env.sh      environnement partagé (venv, CUDA, modèle, vocabulaire)
bin/dictee                interface : start / stop / toggle / daemon-*
bin/dictation-daemon      enveloppe du daemon pour systemd
bin/dictation-daemon.py   daemon à modèle résident (socket Unix)
bin/transcribe.py         repli sans daemon
systemd/                  units utilisateur et correctif CUDA post-veille
config/                   modèle de vocabulaire (jamais la config réelle)
```

## Exploitation

```bash
dictee daemon-status
systemctl --user status dictation-daemon
journalctl --user -u dictation-daemon -f
```

Le correctif `nvidia-uvm-reload.service` recharge `nvidia_uvm` après une mise en
veille : sans lui, CUDA devient inutilisable au réveil sur portable, et le
daemon retombe silencieusement sur le CPU.
