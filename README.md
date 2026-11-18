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
`~/.cache/kvm/transcripts/` (or `$KVM_CACHE_DIR/transcripts/`), matched by the file's **content**,
so re-running, changing options, renaming or moving the file never pays for Gemini twice. On WSL
the Windows kvm cache is shared too, so a file opened from both sides is transcribed once.

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
3. **What to remove**: pick one or more; Enter = all of them (`1,2,3,4`):
   1. Pauses / dead air (free, local)
   2. Filler words: um, ummm, ah, ahhh, er, hmm ... (Gemini)
   3. Repeated words / phrases said 2-3x in a row, and restarted sentences (Gemini). The last take is kept.
   4. AI review (Gemini text model, cheap): fillers the word list misses ("you know", "basically"),
      words that make no sense, stumbles and false starts. **Added automatically whenever 2 or 3 is
      picked.** You approve each suggestion before anything is cut.
4. **Preview**: original vs new length, a breakdown by type, a **timeline** of where the cuts fall,
   the **longest pauses** (with their times), and every cut with the words it removes.
   `e` export, `a` show all cuts, `k` keep some cuts (by number, e.g. `3,5-7`), `u` undo kept cuts,
   `l` long silences (cut / shorten / keep), `c` change what to remove, `s` change pause sensitivity, `q` quit.
5. **Title (optional, Gemini)**: 5 SEO-friendly title ideas from the transcript, each with a different
   hook (curiosity, how-to, list, benefit, bold claim). Pick, edit, regenerate or write your own.
   The title becomes the file name and is embedded in the file; a `_seo.txt` gets the description,
   tags and hashtags. Pause-only runs transcribe once for this (cached). If Google's newest model is
   busy (error 503), kvm moves straight on to another Gemini text model.
6. **Export**: `kvm_output/<name>_kvm.<ext>` plus `<name>_kvm_cuts.txt`, and `<name>_kvm_transcript.md`
   whenever there is a transcript. When a title/SEO pack is chosen, all output files (export, cuts report,
   transcript, SEO pack) are kept together in their own folder: `kvm_output/<title>/`.

**Transcript as Markdown.** One numbered, timestamped line per sentence, verbatim (fillers, repeats
and false starts kept), with `[pause N.Ns]` marks, e.g.
`[2] (00:16.6) If you come to the Lovable Docs, I would leave the link in the in in the course material.`
Read it yourself, or paste it into an AI to spot messy sentences. `kvm talk.mp4 --transcript` writes
only this file (free when the transcript is already saved; otherwise one Gemini transcription).

**Cut by editing the transcript (free).** Open `kvm_output/<name>_kvm_transcript.md` (or the one from
`--transcript`), strike out what you want gone with `~~ ~~` and save:

    [141] (13:39.3) ~~We could do a P uh PAW is it P [pause 1.1s]~~ We could do PRW.
    ~~[142] (13:46.6) Oh man, come on.~~

Run kvm on the same recording again: the wizard finds the edited transcript and asks to cut
those words (`kvm talk.mp4 --edits`, or `--edits path.md`, in direct mode). Strikes may cover a
word, a whole line or run over several lines. Words said twice in a line are cut where you struck
them. If you also fixed typos in a line, the struck words are found by their text. No Gemini call;
the cuts are aligned to the audio like any word cut, and a transcript with strikes is never
overwritten.

**Word cuts follow the audio.** Gemini's word times are coarse (0.1 s), so each filler/repeat cut
is moved to the quietest moment within 150 ms, the real gap between words. That avoids leftover
"mmm" tails and clipped neighbouring words. `--no-align` turns it off.

**AI review (option 4 / `--ai-review`, automatic with fillers or repeats).** The filler list only
knows fixed words (um, uh, er ...), so whenever you remove fillers or repeats kvm also runs the AI
review on the same transcript. Turn that off with `--no-ai-review` or `KVM_AI_REVIEW=off` (in `.env`
or the environment; also applies to the wizard). A cheap text model (`gemini-2.5-flash-lite`, change with
`GEMINI_REVIEW_MODEL`) reads the Markdown transcript and quotes words to cut, by line, with a reason
(`nonsense`, `false-start`, `repeat`, `filler`) and a confidence. kvm finds the quoted words in that
line, uses their real times and aligns the cuts to the audio. In the wizard you see every suggestion
and approve them (Enter = the ones at 0.8+ confidence, `all`, `none` or numbers); `i` in the preview
changes it later. `--ai-review` alone cuts suggestions with confidence >= `--ai-min-confidence`
(default 0.8); add `--dry-run` to see them all first. One call per file, saved in
`~/.cache/kvm/reviews/`, so re-planning is free. It can only judge words that are in the transcript,
and a word that looks like nonsense may be Gemini mishearing you: approve with care.

**Long silences (10 s+)** are often screen demos where you show something without talking.
They are listed in the preview; choose to cut them, shorten each to a few seconds, or keep them.

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
| `--ai-review` | | AI suggests cuts (missed fillers, nonsense words, stumbles, false starts); cuts those >= `--ai-min-confidence`. On automatically with `--fillers` / `--repeats` / `--all` |
| `--edits [MD]` | | Cut the words you struck out (`~~ ~~`) in the transcript; finds it in `kvm_output/` without a path |
| `--no-ai-review` | | Do not add the AI review automatically to `--fillers` / `--repeats` / `--all` (or `KVM_AI_REVIEW=off`) |
| `--ai-min-confidence N` | `0.8` | Confidence (0-1) an AI suggestion needs to be cut in direct mode |
| `--words-file PATH` | `fillers.txt` | Your own filler list |
| `--audio-only` | | Export only the edited sound |
| `--format EXT` | | `wav`, `flac`, `mp3`, `m4a`, `ogg`, `opus` (implies `--audio-only`) |
| `-o, --output` | `./kvm_output/` | Output folder or file |
| `--silence-threshold DB` | `-35` | Quieter than this counts as a pause. Noisy room? Try `-30`. Cutting into quiet words? Try `-45` |
| `--min-pause SEC` | `0.6` | Only pauses longer than this are shortened |
| `--keep SEC` | `0.2` | Natural pause left on each side of a cut |
| `--long-pauses MODE` | `cut` | Silences of 10 s+: `cut`, `shorten` or `keep` |
| `--long-keep SEC` | `2` | Seconds left by `--long-pauses shorten` |
| `--no-align` | | Use Gemini's word times as-is (don't snap word cuts to the quiet gap) |
| `--transcript` | | Only write `<name>_transcript.md` (`-o notes/talk.md` to choose the file) |
| `--ai-title` | | Name the export with the top AI title + write `<title>_seo.txt` (Gemini) |
| `--dry-run` | | Show the cuts, longest pauses and timeline, write nothing |

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
