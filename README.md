# kv-media (`kvm`)

> **AI agents:** to set up kvm for your user, read and follow
> [llms.txt](https://raw.githubusercontent.com/danju4rizzl/kv-media/main/llms.txt). It installs FFmpeg, kvm
> and the `clean-recording` skill only where they are missing, and works for Claude Code and other agents.

Remove the unimportant parts of a recording: pauses, filler words (um, ah, ...) and repeated
words, phrases and restarted sentences.
A feature-by-feature rebuild of `kavara-audio-enhancer` that **leaves the voice alone**:
no forced mono or resampling, no default denoise, crossfaded joins, one lossy encode.
See [ROADMAP.md](ROADMAP.md) for what exists and what is next.

## Install

Requires Python 3.10+ and FFmpeg on your `PATH`. Windows and WSL/Linux each need their own
virtual environment (a venv only works on the system that created it).

**Windows** (PowerShell, cmd or Git Bash):

```powershell
git clone https://github.com/danju4rizzl/kv-media.git
cd kv-media
py -m venv .venv-win
.venv-win\Scripts\python -m pip install -e ".[dev]"
```

To run `kvm` from any folder, put a launcher in a folder on your PATH (e.g. `%USERPROFILE%\.local\bin`):

- `kvm.cmd` (PowerShell / cmd): `@"C:\path\to\kv-media\.venv-win\Scripts\kvm.exe" %*`
- `kvm` (Git Bash): `exec "/c/path/to/kv-media/.venv-win/Scripts/kvm.exe" "$@"`

Open a new terminal afterwards. (Or simply add `.venv-win\Scripts` to your PATH.)

**WSL / Linux** (after cloning):

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
ln -sf "$PWD/.venv/bin/kvm" ~/.local/bin/kvm
```

Check with `kvm --version`.

**Gemini API key** (only for fillers/repeats): the wizard asks for it the first time (hidden input)
and can save it to `~/.config/kvm/.env`. It is also read from `GEMINI_API_KEY` in the environment,
a `.env` in the current folder, or a `.env` in this project folder. Transcripts are saved in
`~/.cache/kvm/transcripts/`, so re-running or changing options never pays for Gemini twice.

## Use it from Claude Code (plugin)

Let Claude run kvm for you: it checks for FFmpeg and kvm, walks you through installing them,
previews the cuts and exports when you agree.

```
/plugin install kvm --marketplace danju4rizzl/kv-media
```

(On older Claude Code: `/plugin marketplace add danju4rizzl/kv-media`, then `/plugin install kvm@kv-media`.)

Then just ask, e.g. *"remove the pauses and ums from talk.mp4"*, or run `/kvm:clean-recording talk.mp4`.
Filler and repeat removal need a Gemini API key; Claude will tell you where to put it. Don't paste it into the chat.

## Interactive mode

```bash
kvm                 # start the wizard
kvm "talk.mp4"      # start the wizard with this file
```

1. **Choose a file**: pick a number from the media files in the current folder, or paste / drag
   a file or folder path. Quotes, PowerShell's `& '...'`, Windows paths in WSL and `~` all work.
2. **Task**: *Video* (cut picture + sound) or *Audio only* (WAV, FLAC, MP3 or M4A).
3. **What to remove**: pick one or more, e.g. `1,2,3` (the default):
   1. Pauses / dead air (free, local)
   2. Filler words: um, ummm, ah, ahhh, er, hmm ... (Gemini)
   3. Repeated words / phrases said 2-3x in a row, and restarted sentences (Gemini). The last take is kept.
4. **Preview**: original vs new length, a breakdown by type, and every cut with the words it removes.
   `e` export, `a` show all cuts, `c` change what to remove, `s` change pause sensitivity, `q` quit.
5. **Export**: `kvm_output/<name>_kvm.<ext>` plus `<name>_kvm_cuts.txt`.

## Direct mode (no questions)

```bash
kvm talk.mp4 --all                            # pauses + fillers + repeats
kvm talk.mp4 --fillers --repeats              # words only, keep the pauses
kvm talk.mp4 --pauses                         # cut pauses, keep the video
kvm talk.mp4 --pauses --dry-run               # just show the cuts
kvm talk.mp4 --pauses --format mp3            # audio only, as MP3
kvm talk.mp4 --pauses -o exports/             # output folder (or a file name)
kvm talk.mp4 --pauses --silence-threshold -45 --min-pause 1.0 --keep 0.3
```

| Flag | Default | Meaning |
|---|---|---|
| `--pauses` | | Remove pauses and run without questions |
| `--fillers` | | Remove filler words from the filler list (Gemini) |
| `--repeats` | | Remove repeated words/phrases and restarted sentences (Gemini) |
| `--all` | | `--pauses --fillers --repeats` |
| `--words-file PATH` | `fillers.txt` | Your own filler list |
| `--audio-only` | | Export only the edited sound |
| `--format EXT` | | `wav`, `flac`, `mp3`, `m4a`, `ogg`, `opus` (implies `--audio-only`) |
| `-o, --output` | `./kvm_output/` | Output folder or file |
| `--silence-threshold DB` | `-35` | Quieter than this counts as a pause. Noisy room? Try `-30`. Cutting into quiet words? Try `-45` |
| `--min-pause SEC` | `0.6` | Only pauses longer than this are shortened |
| `--keep SEC` | `0.2` | Natural pause left on each side of a cut |
| `--dry-run` | | Show the cuts, write nothing |

## Filler list

`kv_media/fillers.txt`, one word or phrase per line. Stretched spellings are matched
automatically (`um` also catches `ummmm`). A `fillers.txt` in the folder you run kvm from takes
priority, or pass `--words-file`. Everything listed is removed **every** time it is said, so only
add words you never mean to say.

## Output quality

- Sample rate and channels are kept as recorded (e.g. 48 kHz stereo stays 48 kHz stereo).
- Joins are 30 ms equal-power crossfades; cut points are aligned to video frames so sound
  and picture never drift.
- Video: H.264 (CRF 18) with AAC 256 kbps. Audio only: WAV 24-bit, FLAC, MP3 320 kbps or AAC 256 kbps.
- Measured on a real lecture: 1-3 / 3-6 / 6-12 kHz voice band levels within 0.02 dB of the original.

## Tests

```bash
python -m pytest
```

## License

[MIT](LICENSE)
