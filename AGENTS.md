# AGENTS.md — kv-media

Rules for any AI agent (or human) working on this project.

## Process
- **Read `ROADMAP.md` first.** Build only the item marked `Next`, one feature at a time.
- When a feature ships: mark it `Done` in `ROADMAP.md`, add a changelog line, promote the next item.
- Lessons and measurements from the old tool live in
  `../kavara-helper-scripts/kavara-audio-enhancer/AGENTS.md`. Respect them unless re-measured.

## Do no harm to the voice (the reason this rebuild exists)
- Default job is **cut only**. Denoise, speed and loudness are opt-in, never on by default.
- Keep the source sample rate and channels (no forced mono / 44.1 kHz). Work in float.
- **One lossy encode**, at the very end. No 16-bit intermediate files between stages.
- Pad cuts and crossfade joins so word onsets/tails are never clipped.
- Gemini only transcribes; cut decisions are local code. Transcribe once.

## Output
- All output goes to `kvm_output/` in the current directory (or `--output`). Never next to the source.
- `kvm_output/` and media files are gitignored. Never commit media.

## Pipeline order (fixed, as features arrive)
extract -> transcribe + detect cuts (raw audio) -> apply cuts -> denoise -> speed -> loudnorm -> subtitles -> assemble.
Cut timestamps refer to the original recording, so nothing that changes length runs before cuts.

## Engineering
- FFmpeg via subprocess, one final video encode only. Video cut graph uses `trim` + `concat`.
- Tests: `python -m pytest`. FFmpeg tests skip when FFmpeg is missing. Never call real Gemini in tests.
- Secrets live in `.env` (gitignored). Never print or commit them.
- Write files with `encoding="utf-8"`.
- Commit only when the user asks.
