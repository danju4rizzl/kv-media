"""Build and run the single FFmpeg export pass.

Voice-safety rules (see ROADMAP.md "Why the rebuild"):
- Source sample rate and channel layout are kept (no forced mono / resampling).
- Audio stays in FFmpeg's float pipeline and is encoded once, at the end.
- Joins are true crossfades. Each kept piece borrows half a crossfade of audio from the
  (already silent, padded) cut region on each joined side, so the crossfaded audio is exactly
  as long as the video and A/V sync is kept.
- With video, every cut point is snapped to a frame boundary, so per-segment rounding can
  never add up to drift over hundreds of cuts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kv_media import ffmpeg

CROSSFADE_SECONDS = 0.03
VIDEO_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
VIDEO_AUDIO_ARGS = ["-c:a", "aac", "-b:a", "256k"]
AUDIO_ARGS = {
    ".wav": ["-c:a", "pcm_s24le"],
    ".flac": ["-c:a", "flac"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    ".m4a": ["-c:a", "aac", "-b:a", "256k"],
    ".aac": ["-c:a", "aac", "-b:a", "256k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "8"],
    ".opus": ["-c:a", "libopus", "-b:a", "192k"],
}


@dataclass(frozen=True)
class Segment:
    start: float                 # seconds in the source
    end: float
    frame_start: int | None = None
    frame_end: int | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_segments(
    keeps: list[tuple[float, float]], info: ffmpeg.MediaInfo, with_video: bool
) -> list[Segment]:
    """Turn kept time ranges into segments; snap them to video frames when video is exported."""
    if not (with_video and info.has_video and info.fps):
        return [Segment(s, e) for s, e in keeps if e > s]

    fps, offset = info.fps, info.video_offset
    segments: list[Segment] = []
    previous_end = 0
    for start, end in keeps:
        first = max(previous_end, round((start - offset) * fps))
        last = round((end - offset) * fps)
        if last <= first:
            continue
        seg_start = max(0.0, float(offset + first / fps))
        seg_end = min(info.duration, float(offset + last / fps))
        if seg_end <= seg_start:
            continue
        segments.append(Segment(seg_start, seg_end, first, last))
        previous_end = last
    return segments


def build_audio_graph(
    segments: list[Segment], info: ffmpeg.MediaInfo, crossfade: float = CROSSFADE_SECONDS
) -> str:
    """Trim each kept piece (with crossfade handles) and chain acrossfades into [aout]."""
    if not segments:
        raise ValueError("Nothing left to keep.")
    n = len(segments)
    half = crossfade / 2
    source = f"[0:{info.audio_index}]"
    if n == 1:
        s = segments[0]
        return f"{source}atrim=start={s.start:.6f}:end={s.end:.6f},asetpts=PTS-STARTPTS[aout]"
    parts = [f"{source}asplit={n}" + "".join(f"[as{i}]" for i in range(n))]
    for i, s in enumerate(segments):
        a_start = s.start - half if i > 0 else s.start
        a_end = s.end + half if i < n - 1 else s.end
        a_start, a_end = max(0.0, a_start), min(info.duration, a_end)
        parts.append(f"[as{i}]atrim=start={a_start:.6f}:end={a_end:.6f},asetpts=PTS-STARTPTS[a{i}]")
    previous = "[a0]"
    for i in range(1, n):
        label = "[aout]" if i == n - 1 else f"[x{i}]"
        parts.append(f"{previous}[a{i}]acrossfade=d={crossfade:g}:c1=qsin:c2=qsin{label}")
        previous = label
    return ";\n".join(parts)


def build_video_graph(segments: list[Segment], info: ffmpeg.MediaInfo) -> str:
    """Constant frame rate, frame-exact trims, concat into [vout]."""
    if not segments:
        raise ValueError("Nothing left to keep.")
    n = len(segments)
    vsrc = f"[0:{info.video_index}]fps={info.fps}"
    if n == 1:
        s = segments[0]
        return (f"{vsrc},trim=start_frame={s.frame_start}:end_frame={s.frame_end},"
                f"setpts=PTS-STARTPTS[vout]")
    parts = [f"{vsrc},split={n}" + "".join(f"[vs{i}]" for i in range(n))]
    for i, s in enumerate(segments):
        parts.append(f"[vs{i}]trim=start_frame={s.frame_start}:end_frame={s.frame_end},"
                     f"setpts=PTS-STARTPTS[v{i}]")
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]")
    return ";\n".join(parts)


def _audio_codec_args(info: ffmpeg.MediaInfo, codec: list[str]) -> list[str]:
    return [*codec, *(["-ar", str(info.sample_rate)] if info.sample_rate else [])]


def export(
    info: ffmpeg.MediaInfo,
    segments: list[Segment],
    output: Path,
    with_video: bool,
    work_dir: Path,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Write ``output``.

    Audio only: one pass. With video: the edited audio is rendered first to a lossless
    32-bit float WAV, then the video is cut and both are encoded together. Two passes because
    FFmpeg 6.1 drops the tail of the last acrossfade when video is filtered in the same run
    (measured: the final 0.23 s of a real lecture went missing). The float WAV is lossless,
    so the voice is still encoded only once.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    total = sum(s.duration for s in segments)
    tmp = work_dir / f".{output.stem}.kvm-tmp"
    audio_script, video_script = tmp.with_suffix(".audio.txt"), tmp.with_suffix(".video.txt")
    audio_wav = tmp.with_suffix(".wav")
    audio_script.write_text(build_audio_graph(segments, info), encoding="utf-8")

    def scaled(lo: float, hi: float) -> Callable[[float], None] | None:
        return (lambda f: on_progress(lo + (hi - lo) * f)) if on_progress else None

    try:
        if not with_video:
            codec = AUDIO_ARGS.get(output.suffix.lower(), VIDEO_AUDIO_ARGS)
            ffmpeg.run_with_progress(
                ["-y", "-i", str(info.path), *ffmpeg.filter_script_args(audio_script),
                 "-map", "[aout]", "-vn", *_audio_codec_args(info, codec), str(output)],
                "audio export", total, on_progress)
            return

        ffmpeg.run_with_progress(
            ["-y", "-i", str(info.path), *ffmpeg.filter_script_args(audio_script),
             "-map", "[aout]", "-vn", "-c:a", "pcm_f32le", "-rf64", "auto", str(audio_wav)],
            "audio edit", total, scaled(0.0, 0.1))
        video_script.write_text(build_video_graph(segments, info), encoding="utf-8")
        args = ["-y", "-i", str(info.path), "-i", str(audio_wav),
                *ffmpeg.filter_script_args(video_script),
                "-map", "[vout]", "-map", "1:a:0", *VIDEO_ARGS,
                *_audio_codec_args(info, VIDEO_AUDIO_ARGS)]
        if output.suffix.lower() in (".mp4", ".mov"):
            args += ["-movflags", "+faststart"]
        ffmpeg.run_with_progress([*args, str(output)], "video export", total, scaled(0.1, 1.0))
    finally:
        for f in (audio_script, video_script, audio_wav):
            f.unlink(missing_ok=True)
