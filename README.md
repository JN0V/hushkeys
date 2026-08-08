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
sudo apt install -y pulseaudio-utils socat libnotify-bin ydotool wl-clipboard
sudo usermod -aG input $USER     # accès à /dev/uinput — redémarrage requis
./install.sh
```

Puis un raccourci clavier GNOME (*Settings > Keyboard > Custom Shortcuts*) sur
`~/bin/dictee toggle`.

Pour dicter dans un terminal, ajouter un second raccourci — les terminaux collent
avec `Ctrl+Shift+V` :

```
env DICTATION_PASTE_KEYS=ctrl+shift+v /home/<user>/bin/dictee toggle
```

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
- L'appartenance au groupe demande un **redémarrage**, et non une simple
  reconnexion. `systemd --user` survit à la fermeture de session tant qu'un
  processus utilisateur subsiste, or ses groupes supplémentaires sont figés à son
  propre démarrage : tout service `--user` qu'il relance ensuite — dont
  `ydotoold` — hérite de l'ancien jeu de groupes. Se déconnecter et se
  reconnecter ne change rien. Ubuntu 26.04 ne fournissant ni `sg` ni `newgrp`, il
  n'existe pas non plus de rattrapage à chaud.

  Pour le vérifier : `loginctl list-sessions` montre une entrée `manager` dont le
  leader est le `systemd --user`, avec sa date de démarrage d'origine.

Si `ydotoold` ne tourne pas, `dictee` laisse le texte dans le presse-papiers
plutôt que de perdre la transcription.

### Le texte est collé, pas frappé

`ydotool type` est inutilisable sur un clavier non-US. Il émet des codes touches
bruts et suppose une disposition américaine — son propre `--help` l'assume :

> Since there's no way to know how many keyboard layouts are there in the world,
> we're using raw keycodes now.

Sur AZERTY, `KEY_A` produit un `q` : la dictée sort en charabia.

Le texte transite donc par le presse-papiers, et seul un raccourci de collage est
émis. `Ctrl`, `Shift` et `V` occupent la même position physique en AZERTY et en
QWERTY, leurs codes bruts sont donc corrects quelle que soit la disposition. Effet
secondaire appréciable : l'insertion est instantanée, là où la frappe caractère
par caractère demandait plusieurs secondes sur un paragraphe.

La combinaison est paramétrable (`DICTATION_PASTE_KEYS`) parce qu'elle dépend de
l'application visée : les terminaux collent avec `Ctrl+Shift+V`. On ne peut pas
choisir automatiquement — GNOME refuse `org.gnome.Shell.Introspect.GetWindows`
aux appelants non autorisés, il n'y a donc aucun moyen de savoir quelle fenêtre a
le focus.

Le presse-papiers est restauré après collage, mais uniquement s'il contenait du
texte : un contenu non textuel présent avant la dictée est perdu.

Les alternatives ont été écartées : `wtype` ne fonctionne pas sous GNOME (même
protocole wlroots absent), et `dotool`, qui gère les dispositions, n'est pas
packagé dans Ubuntu.

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

Le dépôt peut être cloné n'importe où : le service systemd pointe sur
`~/bin/dictation-daemon`, et l'enveloppe remonte au dépôt par `readlink`. Tout
est installé par liens symboliques, donc un `git pull` suffit à mettre à jour —
il n'y a pas à réinstaller.

---

## Licence

MIT — voir [LICENSE](LICENSE).

Le code ne dérive d'aucun projet existant. En particulier, il ne reprend rien de
[nerd-dictation][nd] (GPL-3.0), qui traite le même besoin par une architecture
entièrement différente : Python, VOSK en flux continu, script unique.

`ydotool` est sous AGPL-3.0, mais il est invoqué comme processus séparé, sans
liaison ni intégration de code — une invocation en ligne de commande reste à
distance de bras et n'étend pas sa licence à l'appelant. Même chose pour
`parecord`, `socat` et `notify-send`. Les dépendances Python — faster-whisper et
CTranslate2 — sont en MIT.

[nd]: https://github.com/ideasman42/nerd-dictation
