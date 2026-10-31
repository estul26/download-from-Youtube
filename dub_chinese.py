#!/usr/bin/env python3
"""Create a Chinese voice track with a local GPT-SoVITS server.

Input: a video and a Chinese SRT whose timing follows the original speech.
Output: an MP4 with two audio tracks, Chinese dubbing (default) and the
original audio, plus the Chinese subtitle as a soft (switchable) track.

GPT-SoVITS must already be running, for example:

    cd ~/GPT-SoVITS && python api_v2.py -a 127.0.0.1 -p 9880

Every synthesized line is cached in --workdir under a name derived from its
text and voice, so rerunning the same command after an interruption resumes
where it stopped instead of synthesizing everything again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

from subtitle_improver import SubtitleError, parse_srt

DEFAULT_API_URL = os.environ.get("GPT_SOVITS_API_URL", "http://127.0.0.1:9880")
SAMPLE_RATE = 48_000
SENTENCE_END = ("。", "！", "？", "!", "?", ".", "…", "；", ";")
MERGE_MAX_GAP_MS = 700
MERGE_MAX_SPAN_MS = 15_000
MERGE_MAX_CHARS = 90
MIN_GAP_MS = 80
TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
ANNOTATION_RE = re.compile(r"\[[^\]]*\]|【[^】]*】|♪+")
SPEAKABLE_RE = re.compile(r"[\w一-鿿]")


class DubError(RuntimeError):
    pass


class ServerUnavailable(DubError):
    pass


@dataclass(frozen=True)
class Segment:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Placement:
    start_ms: int
    tempo: float


@dataclass(frozen=True)
class Voice:
    name: str
    ref_audio: Path
    prompt_text: str
    prompt_lang: str


# ---------- Text and timing ----------
def clean_text(text: str) -> str:
    text = TAG_RE.sub("", text)
    text = ANNOTATION_RE.sub("", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"(?<=[一-鿿，。！？、；：])\s+|\s+(?=[一-鿿，。！？、；：])", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text if SPEAKABLE_RE.search(text) else ""


def build_segments(cues) -> list[Segment]:
    """Turn subtitle cues into speakable segments.

    Short cues that continue the same sentence are merged so the voice does not
    stop mid-sentence, which is common with YouTube's rolling captions.
    Overlapping cues are clipped so segments never overlap.
    """
    items = sorted(
        ((cue.start_ms, cue.end_ms, clean_text(cue.text)) for cue in cues),
        key=lambda item: item[0],
    )
    items = [item for item in items if item[2]]
    merged: list[list] = []
    for start, end, text in items:
        if merged:
            previous = merged[-1]
            if previous[2] == text and start <= previous[1] + MERGE_MAX_GAP_MS:
                previous[1] = max(previous[1], end)  # repeated rolling caption
                continue
            continues = not previous[2].endswith(SENTENCE_END)
            close = start - previous[1] <= MERGE_MAX_GAP_MS
            fits = end - previous[0] <= MERGE_MAX_SPAN_MS and len(previous[2]) + len(text) <= MERGE_MAX_CHARS
            if continues and close and fits:
                joiner = "" if re.search(r"[一-鿿，、]$", previous[2]) else "，"
                if re.match(r"[a-zA-Z0-9]", text) and re.search(r"[a-zA-Z0-9]$", previous[2]):
                    joiner = " "
                previous[1] = max(previous[1], end)
                previous[2] = previous[2] + joiner + text
                continue
        merged.append([start, end, text])

    segments: list[Segment] = []
    for number, (start, end, text) in enumerate(merged):
        if number + 1 < len(merged):
            end = min(end, merged[number + 1][0])
        segments.append(Segment(number, start, max(end, start + 1), text))
    return segments


def plan_timeline(
    segments: list[Segment], durations_ms: list[int], total_ms: int, max_speed: float
) -> list[Placement]:
    """Choose a start time and speed-up factor for every clip.

    A clip may use the time until the next segment starts. If it is still too
    long, it is sped up (up to max_speed); any remaining overflow delays the
    following clips, and the delay disappears at the next natural pause.
    """
    placements: list[Placement] = []
    cursor = 0
    for number, (segment, duration) in enumerate(zip(segments, durations_ms)):
        start = max(segment.start_ms, cursor)
        next_start = segments[number + 1].start_ms if number + 1 < len(segments) else max(total_ms, segment.end_ms)
        available = max(next_start - start - MIN_GAP_MS, 1)
        tempo = 1.0
        if duration > available:
            tempo = min(max_speed, duration / available)
        tempo = round(tempo, 3)
        placements.append(Placement(start, tempo))
        cursor = start + int(round(duration / tempo)) + MIN_GAP_MS
    return placements


# ---------- GPT-SoVITS ----------
def load_voice(directory: Path) -> Voice:
    config_path = directory / "voice.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DubError(f"Could not read {config_path}: {exc}") from exc
    ref_audio = (directory / config.get("ref_audio", "ref.wav")).resolve()
    if not ref_audio.is_file():
        raise DubError(f"Reference audio not found: {ref_audio}")
    prompt_text = str(config.get("prompt_text", "")).strip()
    if not prompt_text:
        raise DubError(f"{config_path} has no prompt_text.")
    return Voice(str(config.get("name", directory.name)), ref_audio, prompt_text, str(config.get("prompt_lang", "zh")))


def check_server(api_url: str) -> None:
    try:
        urllib.request.urlopen(api_url.rstrip("/") + "/tts", timeout=5)
    except urllib.error.HTTPError:
        return  # The server answered (400 for a request without parameters).
    except (urllib.error.URLError, OSError) as exc:
        raise ServerUnavailable(
            f"GPT-SoVITS is not reachable at {api_url}. Start it with:\n"
            "  cd ~/GPT-SoVITS && python api_v2.py -a 127.0.0.1 -p 9880"
        ) from exc


def tts_payload(text: str, voice: Voice, seed: int, speed: float) -> dict:
    return {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": str(voice.ref_audio),
        "prompt_text": voice.prompt_text,
        "prompt_lang": voice.prompt_lang,
        "text_split_method": "cut5",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": speed,
        "seed": seed,
    }


def synthesize(api_url: str, payload: dict, attempts: int = 3, timeout: float = 600) -> bytes:
    """Return WAV bytes. Raises DubError for rejected text, ServerUnavailable otherwise."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    delay = 3.0
    last_error = ""
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            api_url.rstrip("/") + "/tts", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
            if data[:4] != b"RIFF":
                raise DubError("GPT-SoVITS returned something other than WAV audio.")
            return data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 400:
                raise DubError(f"GPT-SoVITS rejected this line: {detail}") from exc
            last_error = f"HTTP {exc.code}: {detail}"
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = str(exc)
        if attempt < attempts:
            time.sleep(delay)
            delay *= 2
    raise ServerUnavailable(f"GPT-SoVITS request failed after {attempts} attempts: {last_error}")


# ---------- Audio helpers ----------
def run_ffmpeg(arguments: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise DubError(f"ffmpeg failed: {result.stderr.strip()[-500:]}")


def probe(path: Path, entries: str, stream: str | None = None) -> str:
    command = ["ffprobe", "-v", "error"]
    if stream:
        command += ["-select_streams", stream]
    command += ["-show_entries", entries, "-of", "default=nw=1:nk=1", str(path)]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""


def media_duration_ms(path: Path) -> int:
    value = probe(path, "format=duration")
    try:
        return int(float(value) * 1000)
    except ValueError as exc:
        raise DubError(f"Could not read the duration of {path}") from exc


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return int(handle.getnframes() * 1000 / handle.getframerate())


TRIM_FILTER = (
    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
    "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse"
)


def normalize_clip(raw: Path, target: Path) -> None:
    """Trim silence and convert to 48 kHz mono 16-bit PCM."""
    temporary = target.with_suffix(".tmp.wav")
    convert = ["-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(temporary)]
    run_ffmpeg(["-i", str(raw), "-af", TRIM_FILTER, *convert])
    if wav_duration_ms(temporary) < 100:  # trimming removed (almost) everything; keep it untrimmed
        run_ffmpeg(["-i", str(raw), *convert])
    temporary.replace(target)


def apply_tempo(source: Path, target: Path, tempo: float) -> None:
    run_ffmpeg(["-i", str(source), "-af", f"atempo={tempo:.3f}", "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(target)])


def write_track(clips: list[tuple[int, Path]], total_ms: int, output: Path) -> None:
    """Write one mono track by placing each clip at its start time."""
    silence_chunk = b"\x00\x00" * SAMPLE_RATE
    written = 0
    with wave.open(str(output), "wb") as track:
        track.setnchannels(1)
        track.setsampwidth(2)
        track.setframerate(SAMPLE_RATE)

        def pad_to(frame: int) -> None:
            nonlocal written
            while written < frame:
                count = min(SAMPLE_RATE, frame - written)
                track.writeframes(silence_chunk[: count * 2])
                written += count

        for start_ms, clip in clips:
            pad_to(int(start_ms * SAMPLE_RATE / 1000))
            with wave.open(str(clip), "rb") as handle:
                frames = handle.readframes(handle.getnframes())
            track.writeframes(frames)
            written += len(frames) // 2
        pad_to(int(total_ms * SAMPLE_RATE / 1000))


def mux(
    video: Path,
    dub_track: Path,
    output: Path,
    subtitle: Path | None,
    original_lang: str,
    voice_name: str,
) -> None:
    has_original_audio = bool(probe(video, "stream=codec_name", "a:0"))
    original_codec = probe(video, "stream=codec_name", "a:0")
    arguments = ["-i", str(video), "-i", str(dub_track)]
    if subtitle:
        arguments += ["-i", str(subtitle)]
    arguments += ["-map", "0:v:0", "-map", "1:a:0"]
    if has_original_audio:
        arguments += ["-map", "0:a:0"]
    if subtitle:
        arguments += ["-map", "2:0"]
    arguments += ["-c:v", "copy", "-c:a:0", "aac", "-b:a:0", "128k"]
    arguments += ["-metadata:s:a:0", "language=chi", "-metadata:s:a:0", f"title=中文配音 ({voice_name})"]
    arguments += ["-disposition:a:0", "default"]
    if has_original_audio:
        if original_codec == "aac":
            arguments += ["-c:a:1", "copy"]
        else:
            arguments += ["-c:a:1", "aac", "-b:a:1", "160k"]
        arguments += ["-metadata:s:a:1", f"language={original_lang}", "-metadata:s:a:1", "title=Original"]
        arguments += ["-disposition:a:1", "0"]
    if subtitle:
        arguments += ["-c:s", "mov_text", "-metadata:s:s:0", "language=chi", "-metadata:s:s:0", "title=中文"]
        arguments += ["-disposition:s:0", "0"]
    partial = output.with_name(output.stem + ".partial.mp4")
    arguments += ["-movflags", "+faststart", str(partial)]
    run_ffmpeg(arguments)
    partial.replace(output)


# ---------- Main workflow ----------
def clip_key(text: str, voice: Voice, seed: int, speed: float) -> str:
    material = json.dumps(
        [text, voice.name, voice.prompt_text, str(voice.ref_audio), seed, speed], ensure_ascii=False
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]


def dub(args: argparse.Namespace) -> Path:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise DubError(f"{tool} is not installed (brew install ffmpeg).")
    video: Path = args.video.resolve()
    output: Path = args.output.resolve()
    if output.exists():
        raise DubError(f"Output already exists: {output}")
    voice = load_voice(args.voice)
    try:
        segments = build_segments(parse_srt(args.srt))
    except SubtitleError as exc:
        raise DubError(str(exc)) from exc
    if not segments:
        raise DubError("The subtitle file has no speakable lines.")
    total_ms = media_duration_ms(video)
    check_server(args.api_url)

    clips_dir: Path = args.workdir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[Path | None] = []
    skipped = 0
    started = time.monotonic()
    done_new = 0
    print(f"Voice: {voice.name}  |  {len(segments)} lines  |  server {args.api_url}")
    for segment in segments:
        key = clip_key(segment.text, voice, args.seed, args.speed)
        final = clips_dir / f"{segment.index:05d}_{key}.wav"
        rejected = clips_dir / f"{segment.index:05d}_{key}.rejected"
        label = f"[{segment.index + 1}/{len(segments)}]"
        if final.exists():
            normalized.append(final)
            continue
        if rejected.exists():
            normalized.append(None)
            skipped += 1
            continue
        try:
            data = synthesize(args.api_url, tts_payload(segment.text, voice, args.seed, args.speed))
        except ServerUnavailable:
            raise
        except DubError as exc:
            print(f"{label} skipped: {exc}")
            rejected.write_text(str(exc), encoding="utf-8")
            normalized.append(None)
            skipped += 1
            continue
        raw = clips_dir / f"{segment.index:05d}_{key}.raw.wav"
        raw.write_bytes(data)
        normalize_clip(raw, final)
        raw.unlink()
        normalized.append(final)
        done_new += 1
        elapsed = time.monotonic() - started
        remaining = len(segments) - segment.index - 1
        eta = elapsed / done_new * remaining
        print(f"{label} {segment.text[:30]}  (about {int(eta // 60)} min left)")

    usable = [(segment, clip) for segment, clip in zip(segments, normalized) if clip is not None]
    if not usable:
        raise DubError("No line could be synthesized.")
    durations = [wav_duration_ms(clip) for _, clip in usable]
    placements = plan_timeline([segment for segment, _ in usable], durations, total_ms, args.max_speed)

    timed_dir = args.workdir / "timed"
    timed_dir.mkdir(exist_ok=True)
    placed: list[tuple[int, Path]] = []
    sped_up = 0
    for (segment, clip), placement in zip(usable, placements):
        if placement.tempo > 1.0:
            sped_up += 1
            target = timed_dir / f"{clip.stem}_{placement.tempo:.3f}.wav"
            if not target.exists():
                apply_tempo(clip, target, placement.tempo)
            placed.append((placement.start_ms, target))
        else:
            placed.append((placement.start_ms, clip))

    track = args.workdir / "chinese_track.wav"
    print("Building the Chinese audio track...")
    write_track(placed, total_ms, track)
    print("Writing the dual-audio MP4...")
    mux(video, track, output, args.srt if args.embed_subtitle else None, args.original_lang, voice.name)
    print(f"Done: {output}")
    if sped_up:
        print(f"{sped_up} line(s) were sped up to fit the original timing (max {args.max_speed}x).")
    if skipped:
        print(f"{skipped} line(s) could not be synthesized and are silent.")
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--srt", type=Path, required=True, help="Chinese subtitle with the original timing.")
    parser.add_argument("--voice", type=Path, required=True, help="Voice folder containing voice.json and ref.wav.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True, help="Cache folder; reuse it to resume.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--original-lang", default="eng", help="ISO 639-2 code of the original audio (default eng).")
    parser.add_argument("--max-speed", type=float, default=1.35, help="Largest speed-up used to fit timing.")
    parser.add_argument("--speed", type=float, default=1.0, help="GPT-SoVITS speed_factor for every line.")
    parser.add_argument("--seed", type=int, default=1234, help="Fixed seed keeps the voice consistent.")
    parser.add_argument("--no-embed-subtitle", dest="embed_subtitle", action="store_false")
    args = parser.parse_args(argv)
    if not 1.0 <= args.max_speed <= 2.0:
        parser.error("--max-speed must be between 1.0 and 2.0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        dub(args)
    except ServerUnavailable as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Finished lines are cached; rerun the same command to resume.", file=sys.stderr)
        return 3
    except DubError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Finished lines are cached; rerun the same command to resume.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
