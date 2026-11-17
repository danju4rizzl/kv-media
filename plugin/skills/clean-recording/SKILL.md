---
name: clean-recording
description: Clean up an audio or video recording by removing pauses / dead air, filler words (um, uh, ah, er, hmm) and repeated words, phrases or restarted sentences, using the kvm command-line tool (kv-media). Use when the user wants to tighten, trim or clean a recording, podcast, lecture, voice-over, screencast or interview; remove silences, ums or stutters; or set up kvm or FFmpeg for that. Also walks through installing FFmpeg and kvm if they are missing.
argument-hint: "[file] [what to remove, e.g. 'pauses and ums']"
---

# Clean a recording with kvm

`kvm` (from kv-media) cuts the unimportant parts out of a recording without touching the voice:
no resampling, no denoise, crossfaded joins, one final encode. You run it for the user in
**direct mode** (flags, no questions). Never start the interactive wizard (`kvm` with no
removal flags): your shell has no keyboard input, so it would just cancel.

If the user gave arguments, they are: `$ARGUMENTS`

## 1. Check the setup

Run these checks first (they are quick and change nothing):

```bash
ffmpeg -version     # first line only is enough
ffprobe -version
kvm --version
```

- All three work → go to step 2.
- `ffmpeg`/`ffprobe` missing → **Install FFmpeg** below.
- `kvm` missing → **Install kvm** below.

### Install FFmpeg

Detect the OS, tell the user which command you will run and why, and get their OK before
installing anything.

| OS | Command |
|---|---|
| Windows | `winget install --id Gyan.FFmpeg -e` (alternatives: `choco install ffmpeg`, `scoop install ffmpeg`) |
| macOS | `brew install ffmpeg` (no Homebrew? point them to https://brew.sh first) |
| Debian / Ubuntu / WSL | `sudo apt update && sudo apt install -y ffmpeg` |
| Fedora | `sudo dnf install -y ffmpeg` (may need RPM Fusion) |
| Arch | `sudo pacman -S ffmpeg` |

Notes:
- Commands needing `sudo` ask for a password you cannot type. Ask the user to run them
  themselves; in Claude Code they can type `! <command>` in the prompt.
- On Windows, winget updates PATH only for **new** terminals. If `ffmpeg -version` still fails
  right after installing, tell the user to restart Claude Code (or their terminal/editor), then
  check again. Do not hunt for the binary and hard-code its path.
- Confirm with `ffmpeg -version` before moving on.

### Install kvm

Needs Python 3.10+. Check with `python --version` (Windows: also try `py --version`; Linux/macOS: `python3`).
Prefer an isolated tool install so it does not touch the user's other Python packages, and
pick the first that is available:

```bash
uv tool install git+https://github.com/danju4rizzl/kv-media
pipx install git+https://github.com/danju4rizzl/kv-media
python -m pip install --user git+https://github.com/danju4rizzl/kv-media
```

Confirm with `kvm --version`. If the command is not found after a `pip --user` install, the
user's script folder is not on PATH: show them the folder pip reported and how to add it, or
run `python -m kv_media` instead of `kvm` for this session.

### Gemini API key (only for fillers and repeats)

Pause removal is free and local. Filler and repeat removal send the audio to Google Gemini to
transcribe it, which needs `GEMINI_API_KEY`. kvm looks for it in the environment, then
`.env` in the current folder, then `~/.config/kvm/.env`.

**Never ask the user to paste the key into the chat, and never print, echo or `cat` a file
that holds it.** If kvm reports that a key is missing:

1. Tell them where to get a key: https://aistudio.google.com/apikey
2. Create the config file with an empty value (do not overwrite an existing file):
   `~/.config/kvm/.env` (Windows: `%USERPROFILE%\.config\kvm\.env`) containing `GEMINI_API_KEY=`
3. Ask them to open that file in their editor, paste the key after `=`, and save. Then rerun.

Offer to start with pauses only while they sort out the key.

## 2. Work out the job

From the user's request, choose:

| They want to remove | Flags |
|---|---|
| silences, dead air, long pauses | `--pauses` |
| um, uh, ah, er, hmm | `--fillers` |
| stutters, repeated words/phrases, restarted sentences (last take kept) | `--repeats` |
| "everything", "clean it up" | `--all` |

Output options:
- Video in, video out is the default. For sound only: `--audio-only`, or `--format wav|flac|mp3|m4a|ogg|opus`.
- Output goes to `./kvm_output/<name>_kvm.<ext>` plus `<name>_kvm_cuts.txt`. Use `-o PATH`
  for another folder or file name.

If no file was named, list media files in the current folder and ask which one. Quote paths
that contain spaces.

## 3. Preview first (always)

Run a dry run. It writes nothing:

```bash
kvm "talk.mp4" --pauses --fillers --dry-run
```

Fillers/repeats on a long recording can take a few minutes (upload + transcription), so give
the command a generous timeout (up to 10 minutes) or run it in the background. The transcript is
cached in `~/.cache/kvm/transcripts/`, so later runs on the same file do not call Gemini again.

Summarise the output for the user instead of pasting the whole cut table: original vs new
length, time removed per type, and a few example cuts (especially the removed words). Then ask:
export as is, change what to remove, or adjust pause sensitivity.

## 4. Adjust if asked

| User says | Change |
|---|---|
| cutting into quiet words / too aggressive | `--silence-threshold -45` (lower = only quieter sounds count as pauses) |
| noisy room, pauses not detected | `--silence-threshold -30` |
| too choppy, cut every small gap | raise `--min-pause` (default 0.6 s), e.g. `1.0` |
| joins feel rushed | raise `--keep` (default 0.2 s of pause kept on each side), e.g. `0.35` |
| a word is wrongly treated as a filler | use a custom list: copy the default `fillers.txt`, edit it, pass `--words-file PATH` |

Rerun the dry run after each change and summarise what changed.

## 5. Export

Run the same command without `--dry-run`. Video export re-encodes (H.264 CRF 18 + AAC 256k),
which can take a while on long videos, so use a long timeout or run it in the background.
Report the saved file and the cut report path printed by kvm.

## Things to avoid

- Do not run `kvm` without `--pauses`, `--fillers`, `--repeats` or `--all` (that opens the wizard).
- Do not write output next to the source or overwrite the original; keep kvm's default output folder unless the user asks otherwise.
- Do not install anything without telling the user what and why first.
- Do not expose the Gemini key in any command, output or message.
