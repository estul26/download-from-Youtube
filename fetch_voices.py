#!/usr/bin/env python3
"""Pick free female Mandarin reference voices for Chinese dubbing.

The voices come from AISHELL-3 (https://www.openslr.org/93/), a studio-recorded
Mandarin corpus released under the Apache License 2.0. The script:

1. Downloads data_aishell3.tgz (about 19 GB) with resume support, unless an
   existing archive is given with --archive.
2. Streams through the archive once, without extracting it, and keeps the
   cleanest 6-9.5 second sentence for every speaker.
3. Uses the corpus speaker metadata to keep only female speakers and writes a
   short list of candidates to voices/_candidates/.
4. Lets you audition candidates (macOS afplay) and save the ones you like as
   voices/<name>/ref.wav plus voices/<name>/voice.json.

After saving voices, the archive and voices/_work can be deleted; each saved
voice is only a few hundred kilobytes.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tarfile
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:  # numpy is optional; it only makes the one-time scan faster.
    import numpy as _np
except ImportError:  # pragma: no cover - exercised only without numpy
    _np = None

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_VOICES_DIR = SCRIPT_DIR / "voices"
DEFAULT_URLS = (
    "https://openslr.elda.org/resources/93/data_aishell3.tgz",
    "https://us.openslr.org/resources/93/data_aishell3.tgz",
    "https://openslr.trmal.net/resources/93/data_aishell3.tgz",
)
SOURCE_PAGE = "https://www.openslr.org/93/"
LICENSE_NAME = "Apache-2.0"

MIN_SECONDS = 6.0
MAX_SECONDS = 9.5
TARGET_SECONDS = 7.5
SCORED_PER_SPEAKER = 10
FRAME_SECONDS = 0.03
PINYIN_RE = re.compile(r"^[a-zA-Z]+[1-5]$")
SPEAKER_RE = re.compile(r"^SSB\d+$")
VOICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


class VoiceError(RuntimeError):
    pass


@dataclass
class Candidate:
    speaker: str
    utterance: str
    seconds: float
    score: float
    wav_file: str
    gender: str = ""
    age_group: str = ""
    accent: str = ""
    text: str = ""


# ---------- Metadata parsing ----------
def parse_transcript_line(line: str) -> tuple[str, str] | None:
    """Return (utterance id, Chinese text) from an AISHELL-3 content.txt line.

    Lines look like: "SSB00050001.wav<TAB>广 guang3 州 zhou1 女 nv3 ...".
    Pinyin tokens are dropped and the Chinese characters are joined.
    """
    tokens = line.split()
    if len(tokens) < 2 or not tokens[0].lower().endswith(".wav"):
        return None
    utterance = tokens[0][:-4]
    characters = [
        token for token in tokens[1:] if not PINYIN_RE.match(token) and token not in {"%", "$"}
    ]
    text = "".join(characters).strip()
    if not text:
        return None
    return utterance, text


def parse_transcripts(content: str) -> dict[str, str]:
    transcripts: dict[str, str] = {}
    for line in content.splitlines():
        parsed = parse_transcript_line(line)
        if parsed:
            transcripts[parsed[0]] = parsed[1]
    return transcripts


def parse_speaker_info(content: str) -> dict[str, dict[str, str]]:
    """Parse spk-info.txt into {speaker: {gender, age_group, accent}}.

    The parser looks for the SSB speaker id and the gender word on each line,
    so it tolerates comment headers and tab or space separated columns.
    """
    speakers: dict[str, dict[str, str]] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        speaker = next((token for token in tokens if SPEAKER_RE.match(token)), "")
        if not speaker:
            continue
        gender = ""
        for token in tokens:
            lowered = token.lower()
            if lowered in {"female", "f"}:
                gender = "female"
            elif lowered in {"male", "m"}:
                gender = "male"
        others = [token for token in tokens if token != speaker and token.lower() not in {"female", "f", "male", "m"}]
        age_group = others[0] if others else ""
        accent = others[1] if len(others) > 1 else ""
        speakers[speaker] = {"gender": gender, "age_group": age_group, "accent": accent}
    return speakers


# ---------- Audio quality scoring ----------
def read_pcm16_mono(data: bytes) -> tuple[list[int] | object, int, float]:
    """Return (samples, sample_rate, seconds) for a 16-bit PCM WAV."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if width != 2:
        raise VoiceError("Only 16-bit PCM WAV files are supported.")
    if _np is not None:
        samples = _np.frombuffer(raw, dtype="<i2").astype(_np.float64)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
    else:
        values = array("h")
        values.frombytes(raw)
        if sys.byteorder == "big":
            values.byteswap()
        samples = list(values[::channels])
    return samples, rate, frames / float(rate) if rate else 0.0


def frame_levels_db(samples: object, rate: int) -> list[float]:
    frame = max(1, int(rate * FRAME_SECONDS))
    levels: list[float] = []
    if _np is not None:
        array_samples = samples  # type: ignore[assignment]
        usable = (len(array_samples) // frame) * frame
        if usable == 0:
            return []
        blocks = array_samples[:usable].reshape(-1, frame)
        rms = _np.sqrt((blocks**2).mean(axis=1))
        return [20 * math.log10(max(value, 1.0) / 32768.0) for value in rms.tolist()]
    sequence = samples  # type: ignore[assignment]
    for start in range(0, len(sequence) - frame + 1, frame):
        block = sequence[start : start + frame : 4]  # stride keeps pure Python fast enough
        if not block:
            continue
        rms = math.sqrt(sum(value * value for value in block) / len(block))
        levels.append(20 * math.log10(max(rms, 1.0) / 32768.0))
    return levels


def score_wav(data: bytes) -> tuple[float, float] | None:
    """Return (score, seconds), or None when the clip is unsuitable.

    Higher scores mean steadier speech: few internal pauses, no clipping,
    a healthy level, and a length close to 7.5 seconds.
    """
    try:
        samples, rate, seconds = read_pcm16_mono(data)
    except (wave.Error, EOFError, VoiceError):
        return None
    if not (MIN_SECONDS <= seconds <= MAX_SECONDS):
        return None
    peak = float(max(abs(min(samples)), abs(max(samples)))) if len(samples) else 0.0
    if peak <= 0 or peak >= 32700:  # silent or clipped
        return None

    levels = frame_levels_db(samples, rate)
    if not levels:
        return None
    loudest = max(levels)
    if loudest < -30:  # too quiet to be a useful reference
        return None
    threshold = loudest - 35
    voiced = [level > threshold for level in levels]
    try:
        first = voiced.index(True)
        last = len(voiced) - 1 - voiced[::-1].index(True)
    except ValueError:
        return None
    inner = voiced[first : last + 1]
    speech_ratio = sum(inner) / len(inner)
    longest_pause = 0
    current = 0
    for is_voiced in inner:
        current = 0 if is_voiced else current + 1
        longest_pause = max(longest_pause, current)
    longest_pause_seconds = longest_pause * FRAME_SECONDS
    if longest_pause_seconds > 0.6:
        return None
    voiced_levels = [level for level, is_voiced in zip(levels, voiced) if is_voiced]
    mean_level = sum(voiced_levels) / len(voiced_levels)
    spread = math.sqrt(sum((level - mean_level) ** 2 for level in voiced_levels) / len(voiced_levels))

    score = (
        speech_ratio * 2.0
        - longest_pause_seconds * 1.5
        - abs(seconds - TARGET_SECONDS) * 0.15
        - max(0.0, spread - 8.0) * 0.05
        - max(0.0, -22.0 - mean_level) * 0.05
    )
    return round(score, 4), round(seconds, 3)


# ---------- Download ----------
def download_archive(destination: Path, urls: Iterable[str]) -> Path:
    curl = shutil.which("curl")
    if not curl:
        raise VoiceError("curl is required to download AISHELL-3.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    if destination.exists():
        return destination
    for url in urls:
        print(f"Downloading {url}")
        print("The file is about 19 GB. Interrupting is safe; rerun to resume.")
        result = subprocess.run(
            [curl, "-L", "--fail", "--retry", "5", "--retry-delay", "5", "-C", "-", "-o", str(partial), url],
            check=False,
        )
        if result.returncode == 0:
            partial.rename(destination)
            return destination
        print(f"Download from {url} failed (curl exit {result.returncode}). Trying the next mirror...")
    raise VoiceError("Could not download AISHELL-3 from any mirror. Rerun to resume, or pass --url.")


# ---------- Archive scan ----------
def scan_archive(archive: Path, work_dir: Path, per_speaker: int = SCORED_PER_SPEAKER) -> dict:
    """Stream the archive once and keep the best clip per speaker on disk."""
    clips_dir = work_dir / "best"
    clips_dir.mkdir(parents=True, exist_ok=True)
    best: dict[str, Candidate] = {}
    scored_count: dict[str, int] = {}
    transcripts: dict[str, str] = {}
    speaker_info: dict[str, dict[str, str]] = {}
    seen = 0

    with tarfile.open(archive, "r|gz") as stream:
        for member in stream:
            if not member.isfile():
                continue
            name = member.name
            lower = name.lower()
            if lower.endswith("spk-info.txt") or lower.endswith("spk_info.txt"):
                handle = stream.extractfile(member)
                if handle:
                    speaker_info.update(parse_speaker_info(handle.read().decode("utf-8", "replace")))
                continue
            if lower.endswith("content.txt"):
                handle = stream.extractfile(member)
                if handle:
                    transcripts.update(parse_transcripts(handle.read().decode("utf-8", "replace")))
                continue
            if not lower.endswith(".wav"):
                continue
            seen += 1
            if seen % 5000 == 0:
                print(f"  scanned {seen} recordings, {len(best)} speakers so far")
            speaker = Path(name).parent.name
            if scored_count.get(speaker, 0) >= per_speaker:
                continue
            # 16-bit mono WAV size prefilter (rates between 16 and 48 kHz).
            if member.size < 16_000 * 2 * MIN_SECONDS or member.size > 48_000 * 2 * MAX_SECONDS + 4096:
                continue
            handle = stream.extractfile(member)
            if not handle:
                continue
            data = handle.read()
            result = score_wav(data)
            if result is None:
                continue
            scored_count[speaker] = scored_count.get(speaker, 0) + 1
            score, seconds = result
            utterance = Path(name).stem
            current = best.get(speaker)
            if current is None or score > current.score:
                target = clips_dir / f"{speaker}.wav"
                target.write_bytes(data)
                best[speaker] = Candidate(speaker, utterance, seconds, score, str(target))

    for candidate in best.values():
        candidate.text = transcripts.get(candidate.utterance, "")
        info = speaker_info.get(candidate.speaker, {})
        candidate.gender = info.get("gender", "")
        candidate.age_group = info.get("age_group", "")
        candidate.accent = info.get("accent", "")

    summary = {
        "archive": str(archive),
        "speaker_info_found": bool(speaker_info),
        "speakers": {speaker: asdict(candidate) for speaker, candidate in sorted(best.items())},
    }
    (work_dir / "scan.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def choose_candidates(summary: dict, count: int) -> list[Candidate]:
    """Pick the best female candidates, spreading picks across accents."""
    if not summary.get("speaker_info_found"):
        raise VoiceError("The archive did not contain speaker metadata (spk-info.txt); cannot filter female voices.")
    pool = [
        Candidate(**values)
        for values in summary["speakers"].values()
        if values.get("gender") == "female" and values.get("text")
    ]
    pool.sort(key=lambda candidate: candidate.score, reverse=True)
    groups: dict[str, list[Candidate]] = {}
    for candidate in pool:
        groups.setdefault(candidate.accent or "?", []).append(candidate)
    picked: list[Candidate] = []
    while len(picked) < count and any(groups.values()):
        for key in sorted(groups, key=lambda name: -groups[name][0].score if groups[name] else 0):
            if groups[key] and len(picked) < count:
                picked.append(groups[key].pop(0))
    picked.sort(key=lambda candidate: candidate.score, reverse=True)
    return picked


def write_candidates(candidates: list[Candidate], voices_dir: Path) -> Path:
    directory = voices_dir / "_candidates"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    entries = []
    for number, candidate in enumerate(candidates, start=1):
        target = directory / f"{number:02d}.wav"
        shutil.copyfile(candidate.wav_file, target)
        entry = asdict(candidate)
        entry["number"] = number
        entry["wav_file"] = str(target)
        entries.append(entry)
    manifest = directory / "candidates.json"
    manifest.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


# ---------- Saving and audition ----------
def prompt_text_for(text: str) -> str:
    text = text.strip()
    return text if text.endswith(("。", "！", "？")) else text + "。"


def save_voice(entry: dict, name: str, voices_dir: Path) -> Path:
    if not VOICE_NAME_RE.match(name) or name.startswith("_"):
        raise VoiceError("Voice names may use letters, digits, '-' and '_' (up to 40 characters).")
    target = voices_dir / name
    if target.exists():
        raise VoiceError(f"A voice named {name!r} already exists.")
    target.mkdir(parents=True)
    shutil.copyfile(entry["wav_file"], target / "ref.wav")
    voice = {
        "name": name,
        "ref_audio": "ref.wav",
        "prompt_text": prompt_text_for(entry["text"]),
        "prompt_lang": "zh",
        "gender": entry.get("gender", ""),
        "source": f"AISHELL-3 speaker {entry['speaker']}, utterance {entry['utterance']}",
        "source_url": SOURCE_PAGE,
        "license": LICENSE_NAME,
    }
    (target / "voice.json").write_text(json.dumps(voice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def play(path: str) -> None:
    player = shutil.which("afplay")
    if not player:
        print(f"  (no afplay here; open the file to listen: {path})")
        return
    subprocess.run([player, path], check=False)


def describe(entry: dict) -> str:
    details = ", ".join(
        part
        for part in (
            f"age group {entry['age_group']}" if entry.get("age_group") else "",
            f"accent {entry['accent']}" if entry.get("accent") else "",
            f"{entry['seconds']:.1f}s",
        )
        if part
    )
    return f"{entry['number']:>2}) {entry['speaker']} ({details})\n    {entry['text']}"


def pick_interactively(manifest: Path, voices_dir: Path) -> int:
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    by_number = {entry["number"]: entry for entry in entries}
    saved = 0
    print()
    print("Candidates (female, AISHELL-3, Apache-2.0):")
    for entry in entries:
        print(describe(entry))
    print()
    print("Commands: <number> play | s <number> save | l list | q quit")
    while True:
        try:
            command = input("> ").strip()
        except EOFError:
            break
        if command in {"q", "quit"}:
            break
        if not command:
            continue
        if command == "l":
            for entry in entries:
                print(describe(entry))
            continue
        parts = command.split()
        try:
            if len(parts) == 1 and parts[0].isdigit():
                entry = by_number[int(parts[0])]
                print(f"Playing {entry['number']:02d} ...")
                play(entry["wav_file"])
            elif len(parts) == 2 and parts[0] == "s" and parts[1].isdigit():
                entry = by_number[int(parts[1])]
                default = f"female-{entry['number']:02d}"
                name = input(f"Name for this voice [{default}]: ").strip() or default
                path = save_voice(entry, name, voices_dir)
                saved += 1
                print(f"Saved to {path}")
            else:
                print("Commands: <number> play | s <number> save | l list | q quit")
        except KeyError:
            print("No candidate with that number.")
        except VoiceError as exc:
            print(exc)
    return saved


def save_top(manifest: Path, voices_dir: Path, count: int) -> int:
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    saved = 0
    for entry in entries[:count]:
        name = f"female-{entry['number']:02d}"
        try:
            save_voice(entry, name, voices_dir)
            saved += 1
            print(f"Saved {name}")
        except VoiceError as exc:
            print(exc)
    return saved


# ---------- Command line ----------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voices-dir", type=Path, default=DEFAULT_VOICES_DIR)
    parser.add_argument("--archive", type=Path, help="Use an existing data_aishell3.tgz instead of downloading.")
    parser.add_argument("--download-dir", type=Path, default=Path.home() / "Downloads", help="Where to store the archive.")
    parser.add_argument("--url", action="append", help="Download URL (repeatable). Defaults to OpenSLR mirrors.")
    parser.add_argument("--count", type=int, default=12, help="Number of candidates to offer (default 12).")
    parser.add_argument("--pick", action="store_true", help="Skip download/scan and pick from existing candidates.")
    parser.add_argument("--rescan", action="store_true", help="Rescan the archive even if a scan result exists.")
    parser.add_argument("--save-top", type=int, metavar="N", help="Save the top N candidates without prompting.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    voices_dir: Path = args.voices_dir
    work_dir = voices_dir / "_work"
    manifest = voices_dir / "_candidates" / "candidates.json"
    try:
        if not args.pick:
            scan_file = work_dir / "scan.json"
            if scan_file.exists() and not args.rescan:
                print(f"Using previous scan: {scan_file}")
                summary = json.loads(scan_file.read_text(encoding="utf-8"))
            else:
                archive = args.archive or download_archive(
                    args.download_dir / "data_aishell3.tgz", args.url or DEFAULT_URLS
                )
                if not archive.exists():
                    raise VoiceError(f"Archive not found: {archive}")
                print(f"Scanning {archive} (one pass, no extraction; this takes a while)...")
                summary = scan_archive(archive, work_dir)
            candidates = choose_candidates(summary, args.count)
            if not candidates:
                raise VoiceError("No suitable female candidates were found.")
            manifest = write_candidates(candidates, voices_dir)
        elif not manifest.exists():
            raise VoiceError("No candidates yet. Run without --pick first.")

        saved = save_top(manifest, voices_dir, args.save_top) if args.save_top else pick_interactively(manifest, voices_dir)
    except VoiceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Rerun to resume the download or pick again with --pick.")
        return 130

    print()
    print(f"Saved {saved} voice(s) in {voices_dir}.")
    if saved:
        print("When you are happy with your voices you can delete the AISHELL-3 archive")
        print(f"and {work_dir} to free space. Rerun with --pick to choose more later.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
