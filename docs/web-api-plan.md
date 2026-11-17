# Plan: kvm Web API + Docker + Hugging Face Space

Roadmap item **11** (see [ROADMAP.md](../ROADMAP.md)). Status: `Planned`. Written 2026-11-17.

Goal: a small **free-to-host demo** of kvm. Someone uploads a recording, picks what to remove,
previews the cuts and downloads the cleaned file. It is the same engine as the CLI, behind HTTP.

---

## 1. Decisions (agreed 2026-11-17)

| Topic | Decision | Why |
|---|---|---|
| Framework | **FastAPI** + uvicorn | Async uploads, automatic OpenAPI docs at `/docs`, small. |
| Agent framework | **None.** The API calls `kv_media.pipeline` directly | kvm is a fixed pipeline, not an open-ended agent. Claude Agent SDK / ADK would add weight and cost for no gain. |
| Word engine | **Gemini** (unchanged), free tier for the demo | The only fully free model route; already built in. |
| Container | One **Docker** image: `python:3.12-slim` + FFmpeg + kv-media | Same image runs locally, on HF Spaces and on Cloud Run. |
| Host | **Hugging Face Spaces (Docker SDK, free CPU)** | Free, enough RAM/disk for FFmpeg. Cloud Run is the fallback. |
| Front end | Phase A: none (use `/docs`). Phase B: one static HTML page served by FastAPI | Keep the first version tiny. |

Re-check HF's free-tier terms before building (sleep after inactivity, CPU/RAM/disk limits change).

## 2. Non-goals (for this item)
- User accounts, payments, persistent storage, a database.
- GPU, local Whisper, long files (>~20 min) on the free tier.
- Changing any audio behaviour. The "do no harm" rules from AGENTS.md apply unchanged.

---

## 3. Flow

```
Browser / curl
  │ 1. POST /jobs  (multipart: file + options)      -> 202 {job_id}
  │ 2. GET  /jobs/{id}                              -> status: queued | transcribing |
  │                                                    planned | rendering | done | error
  │                                                    + preview (cuts, lengths, breakdown)
  │ 3. POST /jobs/{id}/render                       -> starts the export (after preview)
  │ 4. GET  /jobs/{id}/result                       -> cleaned file (download)
  │    GET  /jobs/{id}/report                       -> <name>_kvm_cuts.txt
  │ 5. DELETE /jobs/{id}  (or auto-expire after 1 h)
  ▼
FastAPI ── background worker (1 at a time) ── kv_media.pipeline.make_plan / execute
                                             └─ FFmpeg, Gemini
```

This mirrors the wizard: plan → preview → export. Preview is free when only pauses are chosen,
and the transcript is cached, so changing options and planning again never pays Gemini twice.
Shortcut: `POST /jobs?render=true` plans and renders in one go (for curl/scripts).

### Endpoints (v1)

| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | `/health` | | `{ok, version, ffmpeg}` |
| POST | `/jobs` | `file`, `audio_only`, `pauses`, `fillers`, `repeats`, `format`, `silence_threshold`, `min_pause`, `keep`, `render` | `202 {job_id}` |
| GET | `/jobs/{id}` | | status, progress 0-1, preview (same data as `report.summary_lines` + cut table as JSON), error text |
| POST | `/jobs/{id}/plan` | changed options (re-plan, cached transcript) | `202` |
| POST | `/jobs/{id}/render` | | `202` |
| GET | `/jobs/{id}/result` | | file stream |
| GET | `/jobs/{id}/report` | | text |
| DELETE | `/jobs/{id}` | | `204` |

---

## 4. Code layout

```
kv_media/web/
  __init__.py
  app.py        FastAPI app, routes, upload limits, static page (phase B)
  jobs.py       in-memory job store + single worker thread, expiry sweeper
  schemas.py    pydantic request/response models (Options, Preview, Cut, JobStatus)
  static/index.html   (phase B) upload form, preview table, download button
Dockerfile
.dockerignore
deploy/hf-space/README.md    Space card (YAML front matter) - see section 6
tests/test_web.py            FastAPI TestClient, Gemini mocked, tiny synthetic clips
```

- Extra install: `pip install kv-media[web]` → `fastapi`, `uvicorn[standard]`, `python-multipart`.
  The CLI stays light. Console script `kvm-web` runs uvicorn.
- Reuse, don't fork: `Job` → `pipeline.make_plan(job, ask_key=None)` → `pipeline.execute(plan, on_progress)`.
  `on_progress` writes into the job record for `GET /jobs/{id}`.

### Small refactors in the existing package (do first, keep CLI behaviour identical)
1. **Writable dirs from env.** `transcribe.CACHE_DIR` and `keys.CONFIG_ENV` are built from
   `Path.home()` at import time. Add `KVM_CACHE_DIR` / `KVM_CONFIG_DIR` env overrides
   (container: `/data/cache` or `/tmp/kvm`).
2. **Quiet engine output.** `transcribe.transcribe` prints with `ui.info`. Route it through a
   callback or `logging` so the server log isn't full of terminal colours.
3. **Preview as data.** Add `report.preview_dict(plan)` (the numbers + cuts as plain dicts) so
   the CLI text and the API JSON come from one source.
4. **Key without prompts.** The server never calls `getpass`: `ask_key=None`, key comes only from
   the `GEMINI_API_KEY` secret (or a per-request header, see section 7).

---

## 5. Dockerfile (sketch)

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# HF Spaces runs the container as uid 1000; give it a home and writable dirs
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH \
    KVM_CACHE_DIR=/tmp/kvm/cache KVM_WORK_DIR=/tmp/kvm/jobs PORT=7860
WORKDIR /home/user/app
COPY --chown=user . .
RUN pip install --no-cache-dir --user ".[web]"
EXPOSE 7860
CMD ["sh", "-c", "uvicorn kv_media.web.app:app --host 0.0.0.0 --port ${PORT}"]
```

- Debian's FFmpeg version must support what `render.py` uses (`acrossfade`, the filter-script
  option chosen by `ffmpeg.major_version()`). Run the integration tests **inside the image**.
- `.dockerignore`: `.venv*`, `kvm_output/`, media files, `.git`, `tests/` (tests run in a separate
  build stage or CI, not in the shipped image).
- `PORT` env lets the same image run on Cloud Run (which sets `PORT=8080`).

## 6. Hugging Face Space

- Create a Space: SDK **Docker**, hardware **CPU basic (free)**, visibility public.
- The Space repo needs a `README.md` with YAML front matter. That would clash with the GitHub
  README, so keep the Space card in `deploy/hf-space/README.md`:
  ```yaml
  ---
  title: kvm - clean up recordings
  emoji: ✂️
  colorFrom: indigo
  colorTo: gray
  sdk: docker
  app_port: 7860
  pinned: false
  license: mit
  ---
  ```
- **Sync from GitHub** with a GitHub Action on push to `main` (or a manual workflow): push the
  repo to the Space with `deploy/hf-space/README.md` copied over the root README. The HF token
  is stored as a GitHub secret `HF_TOKEN`.
- **Space secrets:** `GEMINI_API_KEY` (Settings → Variables and secrets). Never in the image.
- Free Spaces sleep when idle; the first request after that waits for a cold start. Say so on the page.

## 7. Safety and cost on a public demo

- **Gemini quota abuse:** anyone can call a public Space. Options, in order of preference:
  1. Words (fillers/repeats) need the **visitor's own key** in an `X-Gemini-Key` header; pauses stay free.
     The key is used for that job only, never logged or stored.
  2. Or use the Space's key with a per-IP limit (e.g. 3 word-jobs/hour) and a daily cap.
- **Upload limits:** max size (e.g. 200 MB) and max duration (e.g. 20 min), checked with
  `ffprobe` before any work. Only `paths.MEDIA_EXTS` accepted. Stream uploads to disk, not RAM.
- **One job at a time** (free CPU); others queue with a position in the status.
- **Cleanup:** delete uploads, temp files and results after 1 h or on `DELETE`; sweep on startup.
- No user-controlled paths: the server names every file itself (job id); never trust the upload name
  except as the display/download name (sanitised).
- FFmpeg runs with fixed argument lists (already true) and a timeout.

## 8. Milestones

| # | Step | Done when |
|---|---|---|
| A0 | Refactors in §4 (env dirs, quiet output, `preview_dict`) | CLI tests still pass on WSL + Windows |
| A1 | FastAPI app: `/health`, `/jobs` (plan + render), result/report download, in-memory store, expiry | `tests/test_web.py` green with mocked Gemini |
| A2 | Dockerfile + `.dockerignore`; `docker run -p 7860:7860` works locally | Integration tests pass inside the container; a real file round-trips |
| A3 | HF Space created, secrets set, GitHub Action sync | Public URL works; cold start measured |
| B1 | Static page: upload, options, live progress, preview table, download | Usable without `/docs` |
| B2 | Abuse controls from §7 | Limits tested |
| C  | (optional) Cloud Run deploy notes using the same image | |

## 9. Tests
- `TestClient` tests: options validation, job lifecycle, upload limits, unknown job 404,
  expiry, words requested without a key → clear 400.
- Gemini always mocked (AGENTS.md rule); FFmpeg runs on tiny synthetic clips as in `test_integration.py`.
- A `docker build` + smoke test (`/health`, one pause job) in CI later.

## 10. Open questions (decide when starting)
- Visitor's own Gemini key (§7 option 1) vs Space key with limits (option 2)?
- Keep the Space page minimal, or add a waveform/cut timeline in phase B?
- Max file length for the free tier (measure render speed on HF CPU first).

## 11. Prerequisite
Finish roadmap item **2b** (real-file check of filler/repeat cuts) first: the demo should not
show word cuts that haven't been listened to on real recordings.
