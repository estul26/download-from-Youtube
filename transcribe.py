#!/usr/bin/env python3
"""Turn the English speech in a video into an SRT subtitle with faster-whisper.

Runs locally and offline after the model is downloaded once. faster-whisper is
already installed in the GPT-SoVITS environment, so ytgrab.sh runs this script
with that environment's Python:

    ~/.../envs/GPTSoVits/bin/python transcribe.py --video talk.mp4 --output talk.en.srt

Recognized speech is split into short subtitle lines at sentence ends, pauses
and a length limit, which reads better and gives the dubbing tighter timing.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
MAX_CUE_SECONDS = 6.0
MAX_CUE_WORDS = 14
PAUSE_SPLIT_SECONDS = 0.6
SENTENCE_END = (".", "?", "!")
CLAUSE_END = (",", ";", ":")


class TranscribeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


def split_into_cues(words: Iterable[Word]) -> list[tuple[float, float, str]]:
    """Group recognized words into subtitle cues.

    A cue ends at a sentence end, at a pause, or when it gets too long; when it
    is long, it prefers to break after a comma.
    """
    cues: list[tuple[float, float, str]] = []
    current: list[Word] = []

    def flush(upto: int | None = None) -> None:
        nonlocal current
        chunk = current if upto is None else current[:upto]
        rest = [] if upto is None else current[upto:]
        text = "".join(word.text for word in chunk).strip()
        if text:
            cues.append((chunk[0].start, chunk[-1].end, " ".join(text.split())))
        current = rest

    for word in words:
        if not word.text.strip():
            continue
        if current and word.start - current[-1].end >= PAUSE_SPLIT_SECONDS:
            flush()
        current.append(word)
        stripped = word.text.strip()
        too_long = word.end - current[0].start >= MAX_CUE_SECONDS or len(current) >= MAX_CUE_WORDS
        if stripped.endswith(SENTENCE_END):
            flush()
        elif too_long:
            # Break after the last comma if there is one in the second half.
            comma = max(
                (index for index, item in enumerate(current) if item.text.strip().endswith(CLAUSE_END)),
                default=-1,
            )
            flush(comma + 1 if comma >= len(current) // 2 else None)
    if current:
        flush()
    return cues


def format_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    blocks = []
    for number, (start, end, text) in enumerate(cues, start=1):
        end = max(end, start + 0.3)
        blocks.append(f"{number}\n{format_time(start)} --> {format_time(end)}\n{text}")
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    temporary.replace(path)


def extract_audio(video: Path, target: Path) -> float:
    """Write 16 kHz mono audio for Whisper and return its duration in seconds."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise TranscribeError(f"Could not read the audio of {video}: {result.stderr.strip()[-300:]}")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(target)],
        capture_output=True, text=True,
    )
    try:
        return float(probe.stdout.strip())
    except ValueError:
        return 0.0


def transcribe(video: Path, output: Path, model_name: str, language: str) -> int:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscribeError(
            "faster-whisper is not installed in this Python. Install it with:\n"
            f"  {sys.executable} -m pip install faster-whisper"
        ) from exc

    with tempfile.TemporaryDirectory() as temporary:
        audio = Path(temporary) / "audio.wav"
        duration = extract_audio(video, audio)
        print(f"Loading Whisper model {model_name} (downloaded on first use)...")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            str(audio), language=language, word_timestamps=True, vad_filter=True, beam_size=5
        )
        print(f"Transcribing {duration / 60:.1f} min of {language} speech...")
        words: list[Word] = []
        started = time.monotonic()
        last_report = 0.0
        for segment in segments:
            for word in segment.words or []:
                words.append(Word(float(word.start), float(word.end), str(word.word)))
            now = time.monotonic()
            if duration and now - last_report >= 10:
                last_report = now
                done = min(segment.end / duration, 1.0)
                remaining = (now - started) / done * (1 - done) if done > 0 else 0
                print(f"  {done * 100:4.0f}%  (about {int(remaining // 60) + 1} min left)")

    cues = split_into_cues(words)
    if not cues:
        raise TranscribeError("No speech was recognized.")
    write_srt(output, cues)
    print(f"Wrote {len(cues)} subtitle lines to {output}")
    return len(cues)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"faster-whisper model (default {DEFAULT_MODEL}).")
    parser.add_argument("--language", default="en", help="Spoken language code (default en).")
    args = parser.parse_args(argv)
    if args.output.exists():
        print(f"Error: Output already exists: {args.output}", file=sys.stderr)
        return 1
    try:
        transcribe(args.video, args.output, args.model, args.language)
    except TranscribeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
