# kv-media Roadmap

A ground-up rebuild of `kavara-audio-enhancer`, **one feature at a time**.
Command: `kvm` (interactive) or `kvm FILE [flags]`.

Reference implementation (read-only, for lessons learned):
`../kavara-helper-scripts/kavara-audio-enhancer/` — see its `AGENTS.md` and `FEATURE_REQUESTS.md`.

**Status key:** `Done` | `Next` | `Planned` | `Later` | `Idea`

---

## Why the rebuild

The original **degraded the voice** in exported videos. Causes found in its code (2026-11-17):

1. Audio forced to **mono and resampled to 44.1 kHz** on extract (most recordings are 48 kHz).
2. **Spectral denoise on by default** (40%) — "underwater" artifacts, consonants lost.
3. **~5 lossy hand-offs**: a 16-bit WAV written after every stage, then AAC at the end.
4. **Tight cuts** clipped word onsets/tails (s, f, breaths) — choppy speech.
5. Rubber Band speed change adds phasiness, worse above 1.25×.

**Core principle of kv-media: do no harm to the voice.**
- Default job = **cut only**. Denoise, speed and loudness are opt-in, never default.
- Keep the source sample rate and channel layout. Work in float. **One encode at the end.**
- Generous padding around cuts + short **crossfades** at joins (no hard butt-joins).
- Always show a cut preview before writing.
- Before/after band-level check (1-3k / 3-6k / 6-12 kHz vs 100-1k) on a real clip for any audio change.

---

## The interactive flow (target)

```
kvm
 1. Path to file      paste / drag-and-drop a path (like the original)
 2. Task              Video  |  Audio only
 3. What to remove    [x] Pauses / dead air
                      [x] Filler words (um, ummm, ah, ahhh, ...)
                      [x] Repeated words / phrases said 2-3x in a row
 4. Preview cuts      list of what will be removed, total time saved -> confirm
 5. Export            kvm_output/<name>_kvm.<ext>  +  <name>_kvm_cuts.txt
```

Every wizard answer also has a flag, e.g. `kvm talk.mp4 --pauses --fillers --repeats`.

**Word engine decision (2026-11-17): Gemini** (`gemini-3.5-transcribe`, VERBATIM, word timestamps).
FFmpeg alone can find pauses (volume) but cannot hear words. Gemini reliably transcribes fillers;
standard Whisper tends to drop them. Local Whisper stays a later option (item 9).

---

## How we work

1. Build only the item marked **Next**.
2. Agree scope before coding. Ship with tests + README section.
3. Mark it `Done`, add a changelog line, promote the next item. Commit when asked.

---

## Phase 0 — Skeleton

### 0. Project skeleton — `Done`
- `kvm` command, `kvm --version`, package `kv_media/`, pytest, `.gitignore`, `AGENTS.md`.

## Phase 1 — Remove the unimportant parts

### 1. Wizard + pause removal, voice-safe export — `Done` (2026-11-17)
- **Shipped:** `kvm` wizard (file -> task -> removals -> preview with re-tune -> export) and
  `kvm FILE --pauses` direct mode. Modules: `paths`, `ffmpeg`, `pauses`, `cuts`, `render`,
  `pipeline`, `report`, `wizard`, `cli`. 30 tests incl. end-to-end FFmpeg runs.
- **Verified on a real lecture** (85 s, 48 kHz stereo): 48 kHz stereo kept, voice band levels
  within 0.02 dB of the source, audio/video end within 4 ms.
- **FFmpeg 6.1 quirk found:** `acrossfade` loses the tail of the last join when video is filtered in
  the same run. Export is therefore two passes: audio -> lossless float WAV, then video cut + one
  encode. Regression test: `test_short_last_piece_keeps_its_audio`.
- Original spec:
- Wizard steps 1, 2, 3 (pauses only enabled for now; fillers/repeats shown as "coming next"), 4, 5.
- Path input: accepts quoted paths, drag-and-drop, Windows paths, `~`.
- Pause detection: FFmpeg `silencedetect` on the original audio. Defaults: threshold -35 dB,
  min pause 0.6 s, keep ~0.2 s of pause either side.
- Cut engine: one cut-list model shared by every later feature (merge, clamp, pad, sanity checks:
  discard plans removing >80% of a file).
- Export: single FFmpeg pass, `trim` + `concat` for video, audio joins crossfaded (~20-30 ms),
  source sample rate/channels kept, AAC 256k for video (or copy-quality for audio-only: WAV/FLAC
  stay lossless).
- Flags: `--pauses`, `--silence-threshold`, `--min-pause`, `--dry-run`, `-o/--output`, `--audio-only`.
- Cut report `<name>_kvm_cuts.txt`.

### 2. Filler words + repeats (Gemini) — `Done` (2026-11-17, v0.2.0) — needs a real-file check
- **Shipped:** wizard step 3 is multi-select (pauses / fillers / repeats+restarts); preview shows the
  words each cut removes and a breakdown; `c` changes the selection without re-transcribing.
  Flags `--fillers --repeats --all --words-file`. Modules `transcribe.py` (Gemini, chunked, parallel,
  retries, cache in `~/.cache/kvm/transcripts`), `disfluency.py`, `keys.py`, `fillers.txt`.
  `kvm` linked into `~/.local/bin` so it runs from any folder. 74 tests (Gemini mocked).
- **Not yet verified against real Gemini output** (the key lives in the user's other project and
  was not used). First real run: check the cut list on 0001.mp4 and listen to the joins.
- Original spec:
- Plug-in point: add cuts with reason `filler` / `repeat` to the list in `pipeline.make_plan`;
  `cuts.normalize` already merges mixed reasons. Enable options 2 and 3 in `wizard.ask_removals`.
- Filler cuts land mid-speech, so listen to the crossfades there (30 ms may need tuning).
- Transcribe **once** (original audio, chunked + parallel, retry 429/5xx, never fake success).
  Client pinned to `api_version="v1beta"`. Key: env / `.env` -> keyring -> prompt.
- Fillers: matched locally against an **editable word list** `fillers.txt` (um, umm, ummm, uh, ah,
  ahh, ahhh, er, erm, hmm, ...), stretched spellings normalised (`u+m+` etc.). User adds more words later.
- Repeats: same word or phrase said 2-3x in a row -> keep the **last** take.
- Restarts ("I just want to... I just want to click here"): only a single unfinished sentence, <12 s,
  sharing a 3+ word opener (old tool once deleted real content).
- Gemini only transcribes; it never decides cuts.
- Flags: `--fillers`, `--repeats`, `--words-file`.

### 2b. Real-file tuning of word cuts — `Next` (in progress, branch `feat/word-cut-tuning`)
- Findings from real transcripts (2026-11-18): Gemini word times sit on a 0.1 s grid, some words are
  zero-length; some transcripts contain **no fillers at all** even in VERBATIM (Lovable Connectors,
  Before you create a PRD), so audible ums stay. Transcript cache was keyed by path, so WSL and
  Windows paid twice for the same file.
- **Shipped on the branch:** content-keyed transcript cache shared WSL <-> Windows (`KVM_CACHE_DIR`);
  word cuts snapped to the quietest point within 150 ms (`refine.py`, local FFmpeg envelope; cut
  edges on loud audio 76 -> 30 and 11 -> 1 on two real lectures); preview timeline, longest pauses,
  keep single cuts (`k`/`u`), long silences 10 s+ cut / shorten / keep (`--long-pauses`).
- Markdown transcript (`transcript_md.py`, `--transcript`, written with every export that has words):
  numbered timestamped lines = stable ids for a later AI review ("line 12, remove '...'").
- AI review (`review.py`, wizard option 4 / `--ai-review`): `gemini-2.5-flash-lite` reads the Markdown
  and quotes words to cut (nonsense / false-start / repeat / filler) with confidence; kvm matches the
  quote in that line (with "before" words to tell repeats apart), aligns the cut, and the user approves
  (wizard) or `--ai-min-confidence` decides (direct). Cached per transcript + model + prompt version.
  Note: this bends "Gemini only transcribes" on purpose: AI suggests, the user decides.
  Not yet checked against real model output.
- Still to do: listen to the joins on a real export; catch fillers Gemini missed (voiced sounds in
  word gaps -> "possible filler" for review); optional looser repeats; speed-up for long silences
  (screen demos) needs per-segment speed in the render graph; cut markers for video editors.

### 2c. AI title + SEO pack — `Done` (branch `feat/word-cut-tuning`) — needs a real Gemini check
- Wizard step 5 / `--ai-title`: 5 titles with different hooks (keyword in first 40 chars, 40-60 chars,
  honest), SEO pack (keyword, description, tags, hashtags) -> `<title>_seo.txt`; title is the file
  name (slug) and embedded metadata; all files in a pack are grouped into `kvm_output/<title>/` by default (configurable via `--pack-dir` / `--no-pack-dir` or `KVM_PACK_DIR=off`). Text model `gemini-2.5-flash-lite` (`GEMINI_TITLE_MODEL`; cheapest and rarely busy). When a
  model is busy (503/429), missing or replies badly, `gemini_text.py` moves straight on to
  3.1-flash-lite, 2.5-flash, 3.5-flash-lite, 3.5-flash; if all are busy it waits 10 s and tries
  one more round. The AI review uses the same chain. Review quotes may miss up to 2 words.
- Idea: Claude as title engine via `claude -p` (uses a Claude subscription, no API key).

### 3. Batch mode — files, folders, globs — `Planned`

## Phase 2 — Optional enhancements (all opt-in, voice-safe)

### 4. Loudness normalisation — `--lufs N` — `Planned` (two-pass, linear gain only)
### 5. Subtitles — `--subtitles srt|vtt|txt` — `Planned` (re-use the one transcript, remap times)
### 6. Burned-in captions — `--burn-captions` — `Planned` (same final encode)
### 7. Speed change — `--speed X` — `Planned` (measure quality; warn above 1.25×)
### 8. Denoise — `--denoise` — `Planned` (gentle, off by default; evaluate RNNoise/DeepFilterNet first)
### 9. Local Whisper engine — `--engine whisper` — `Later` (offline; needs filler-friendly model)
### 10. Presets (`balanced`, `fast`) — `Later`

## Phase 3 — Visual (from the old FEATURE_REQUESTS.md)
- Auto zoom on cuts / cursor, 9:16 reframing, auto-blur secrets, overlays, chapters — `Idea`
- Rule: run after cuts and speed, in the **same final encode pass**.

## Phase 4 — Web API & free hosting (demo)

### 11. Web API + Docker + Hugging Face Space — `Planned`
- **Full plan: [docs/web-api-plan.md](docs/web-api-plan.md)** (flow, endpoints, layout, Dockerfile,
  HF Space setup, safety, milestones A0-C).
- FastAPI (`kv_media/web/`, optional `[web]` extra) calling `pipeline.make_plan` / `execute` directly.
  No agent framework: kvm is a fixed pipeline.
- Job flow mirrors the wizard: upload → plan + preview (JSON) → render → download file + cut report.
- One Docker image (`python:3.12-slim` + FFmpeg), port 7860; also runs on Cloud Run via `PORT`.
- Hosted free on a **Hugging Face Space** (Docker SDK, CPU basic); `GEMINI_API_KEY` as a Space
  secret; synced from GitHub by an Action. Gemini free tier for word cuts.
- Small refactors first: cache/config dirs from env, engine output via logging, `preview_dict`.
- Prerequisite: item 2b (word cuts checked on real recordings).

---

## Open notes
- Runs on both systems: WSL (FFmpeg 6.1, `.venv`) and Windows (Python 3.14, FFmpeg 8.1,
  `.venv-win`, launchers `kvm.cmd` / `kvm` in `%USERPROFILE%\.local\bin`). Run tests on both.
- Default pause threshold (-35 dB) found only 4 pauses on the test lecture; tune on more real
  recordings before changing the default.

## Changelog
- 2026-11-17 — Skeleton created.
- 2026-11-17 — Plan revised: interactive-first, "do no harm" audio, Gemini chosen as word engine.
  Item 1 = wizard + pause removal; item 2 = fillers + repeats.
- 2026-11-17 — Item 1 shipped (v0.1.0). Item 2 (fillers + repeats via Gemini) is Next.
- 2026-11-17 — Wizard: explains "no pauses found" (0001.mp4 has no gap over 0.36 s).
- 2026-11-17 — Item 2 shipped (v0.2.0); 2b (real-file tuning) is Next.
- 2026-11-17 — Windows support: own venv + launchers, FFmpeg 7+ `-/filter_complex`, Git Bash
  `/c/...` paths, console encoding. Tests pass on WSL (76) and Windows (75 + 1 Linux-only skip).
- 2026-11-17 — Published on GitHub (public, MIT): github.com/danju4rizzl/kv-media.
- 2026-11-17 — Item 11 planned: FastAPI web API + Docker + free Hugging Face Space
  (`docs/web-api-plan.md`). Next is still 2b.
- 2026-11-18 — Item 2c (AI title + SEO pack) built. Item 2b: content-keyed shared cache, word cuts
  aligned to the audio, preview timeline / longest pauses / keep cuts / long-silence modes,
  Markdown transcript (`--transcript`), AI review with approval (`--ai-review`), on automatically with fillers/repeats
  (`--no-ai-review` / `KVM_AI_REVIEW=off` to opt out). Text calls fall back to other models when one is busy.
  Cut by striking words in the transcript (`edits.py`, `--edits`, wizard asks); wizard default
  selects every removal option. 187 tests.
