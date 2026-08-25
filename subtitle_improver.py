#!/usr/bin/env python3
"""Improve a target-language SRT using an English SRT as the meaning reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_CHUNK_SIZE = 60
CONTEXT_CUES = 2
CONTEXT_MILLISECONDS = 10_000
TIMING_RE = re.compile(
    r"^(?P<start>\d{1,3}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[,.]\d{3})(?P<settings>.*)$"
)


class SubtitleError(RuntimeError):
    """A user-facing subtitle or API error."""


class ChunkOutputError(SubtitleError):
    """A retryable problem with one model-generated chunk."""


@dataclass(frozen=True)
class Cue:
    position: int
    number: str
    timing_line: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class NormalizationReport:
    improvements: dict[int, str]
    accepted_ids: frozenset[int]
    missing_ids: frozenset[int]
    blank_nonempty_ids: frozenset[int]
    unexpected_count: int
    duplicate_count: int
    invalid_count: int

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_ids)


def timestamp_to_milliseconds(timestamp: str) -> int:
    normalized = timestamp.replace(",", ".")
    try:
        hours, minutes, seconds = normalized.split(":")
        second_value, milliseconds = seconds.split(".")
        return (
            int(hours) * 3_600_000
            + int(minutes) * 60_000
            + int(second_value) * 1_000
            + int(milliseconds)
        )
    except (ValueError, TypeError) as exc:
        raise SubtitleError(f"Invalid subtitle timestamp: {timestamp}") from exc


def parse_srt(path: Path) -> list[Cue]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise SubtitleError(f"Could not read subtitle file: {path}") from exc

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise SubtitleError(f"Subtitle file is empty: {path}")

    cues: list[Cue] = []
    for block_number, block in enumerate(re.split(r"\n[ \t]*\n+", normalized), start=1):
        lines = block.split("\n")
        timing_index = -1
        timing_match: re.Match[str] | None = None
        for index, line in enumerate(lines):
            match = TIMING_RE.match(line.strip())
            if match:
                timing_index = index
                timing_match = match
                break
        if timing_match is None:
            raise SubtitleError(
                f"Could not find a valid timing line in subtitle block {block_number} of {path.name}."
            )

        number = lines[0].strip() if timing_index > 0 and lines[0].strip() else str(block_number)
        start_ms = timestamp_to_milliseconds(timing_match.group("start"))
        end_ms = timestamp_to_milliseconds(timing_match.group("end"))
        if end_ms <= start_ms:
            raise SubtitleError(
                f"Subtitle block {block_number} has an end time that is not after its start time."
            )
        text = "\n".join(lines[timing_index + 1 :]).strip()
        cues.append(
            Cue(
                position=len(cues),
                number=number,
                timing_line=lines[timing_index].strip(),
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )

    return cues


def format_milliseconds(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def cue_for_prompt(cue: Cue, editable: bool | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": cue.position,
        "start": format_milliseconds(cue.start_ms),
        "end": format_milliseconds(cue.end_ms),
        "text": cue.text,
    }
    if editable is not None:
        result["editable"] = editable
    return result


def relevant_reference_cues(reference: list[Cue], start_ms: int, end_ms: int) -> list[Cue]:
    window_start = max(0, start_ms - CONTEXT_MILLISECONDS)
    window_end = end_ms + CONTEXT_MILLISECONDS
    selected = [cue for cue in reference if cue.end_ms >= window_start and cue.start_ms <= window_end]
    if selected:
        return selected

    # A timing mismatch should not leave the editor without any English context.
    nearest = min(reference, key=lambda cue: min(abs(cue.start_ms - end_ms), abs(cue.end_ms - start_ms)))
    return [nearest]


def output_schema(allowed_ids: set[int]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "cues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "enum": sorted(allowed_ids)},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["cues"],
        "additionalProperties": False,
    }


def editor_instructions(target_language: str) -> str:
    return f"""You are an expert audiovisual subtitle translator and editor.
Improve the editable {target_language} subtitle cues using the supplied English cues as the authoritative meaning reference.

Rules:
- Return exactly one result for every editable target cue ID, and no results for context-only cue IDs.
- Never merge, delete, add, or renumber cues. Return only each cue's improved text.
- If an editable target cue is already empty, return an empty string for that cue. Empty cues are intentional timing placeholders and must stay empty.
- Preserve the meaning, tone, names, numbers, and level of formality in the English reference.
- Keep correct existing target wording when it is already natural; repair mistranslations, omissions, grammar, word choice, and awkward machine translation.
- Keep the text concise enough for its existing screen time. Use at most two natural subtitle lines when practical.
- Do not add explanations, speaker labels, quotation marks, timestamps, IDs, or commentary that are absent from the dialogue.
- Write only in the requested target language. Preserve meaningful subtitle markup only when it is already present.
- For Uyghur (ug), use standard Arabic-script Uyghur unless the requested language explicitly specifies Latin script.
- For zh-Hans, zh-CN, or Simplified Chinese, use Simplified Chinese characters. For zh-Hant, zh-TW, zh-HK, or Traditional Chinese, use Traditional Chinese characters.
- Use the context-only target cues and surrounding English cues to resolve sentences that cross cue boundaries.
"""


def build_payload(
    model: str,
    target_language: str,
    reference: list[Cue],
    target: list[Cue],
    start_index: int,
    end_index: int,
) -> tuple[dict[str, Any], set[int]]:
    editable = target[start_index:end_index]
    context_start = max(0, start_index - CONTEXT_CUES)
    context_end = min(len(target), end_index + CONTEXT_CUES)
    target_context = target[context_start:context_end]
    reference_context = relevant_reference_cues(
        reference,
        editable[0].start_ms,
        editable[-1].end_ms,
    )
    expected_ids = {cue.position for cue in editable}
    document = {
        "target_language": target_language,
        "english_reference_cues": [cue_for_prompt(cue) for cue in reference_context],
        "editable_target_cues": [cue_for_prompt(cue) for cue in editable],
        "target_context_cues": [
            cue_for_prompt(cue)
            for cue in target_context
            if cue.position not in expected_ids
        ],
    }
    payload = {
        "model": model,
        "store": False,
        "reasoning": {"effort": "low"},
        "instructions": editor_instructions(target_language),
        "input": json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        "max_output_tokens": 12_000,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "improved_subtitle_cues",
                "strict": True,
                "schema": output_schema(expected_ids),
            }
        },
    }
    return payload, expected_ids


def api_error_message(body: bytes, fallback: str) -> str:
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
        message = parsed.get("error", {}).get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    except (json.JSONDecodeError, AttributeError):
        pass
    return fallback


def call_responses_api(api_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    retryable_statuses = {408, 409, 429, 500, 502, 503, 504}
    for attempt in range(1, 4):
        request = urllib.request.Request(
            api_url,
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ytgrab-subtitle-improver/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = response.read()
            parsed = json.loads(body.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise SubtitleError("OpenAI returned an unexpected response type.")
            return parsed
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = api_error_message(body, f"OpenAI request failed with HTTP {exc.code}.")
            if exc.code not in retryable_statuses or attempt == 3:
                raise SubtitleError(message) from exc
            retry_after = exc.headers.get("Retry-After", "")
            try:
                delay = min(20.0, max(1.0, float(retry_after)))
            except ValueError:
                delay = float(2 ** (attempt - 1))
            print(f"OpenAI is temporarily unavailable; retrying in {delay:g}s...", file=sys.stderr)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                reason = getattr(exc, "reason", exc)
                raise SubtitleError(f"Could not connect to OpenAI: {reason}") from exc
            delay = float(2 ** (attempt - 1))
            print(f"Network error; retrying in {delay:g}s...", file=sys.stderr)
            time.sleep(delay)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise SubtitleError("OpenAI returned a response that was not valid JSON.") from exc
    raise SubtitleError("OpenAI request failed after retries.")


def extract_structured_output(response: dict[str, Any]) -> dict[str, Any]:
    error = response.get("error")
    if isinstance(error, dict) and error:
        message = error.get("message")
        raise ChunkOutputError(str(message or "OpenAI returned an API error."))
    if response.get("status") == "incomplete":
        details = response.get("incomplete_details") or {}
        reason = details.get("reason", "unknown reason") if isinstance(details, dict) else details
        raise ChunkOutputError(f"OpenAI response was incomplete: {reason}")

    text_parts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                value = content.get("text")
                if isinstance(value, str):
                    text_parts.append(value)
    if not text_parts:
        raise ChunkOutputError("OpenAI returned no subtitle text.")
    try:
        parsed = json.loads("".join(text_parts))
    except json.JSONDecodeError as exc:
        raise ChunkOutputError("OpenAI's structured subtitle result was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ChunkOutputError("OpenAI returned an unexpected subtitle result.")
    return parsed


def validate_improvements(
    result: dict[str, Any],
    expected_ids: set[int],
    original_text_by_id: dict[int, str],
) -> NormalizationReport:
    if set(original_text_by_id) != expected_ids:
        raise SubtitleError("Internal error: target cue IDs do not match the requested chunk.")
    raw_cues = result.get("cues")
    if not isinstance(raw_cues, list):
        raise ChunkOutputError("OpenAI's result did not contain a cue list.")

    # Model output is probabilistic even when its JSON shape is constrained.
    # Start from a lossless copy of the original chunk, then apply only edits
    # that are safe and belong to the requested cue IDs.
    improvements = dict(original_text_by_id)
    accepted_ids: set[int] = set()
    blank_nonempty_ids: set[int] = set()
    unexpected_count = 0
    duplicate_count = 0
    invalid_count = 0
    for item in raw_cues:
        if not isinstance(item, dict):
            invalid_count += 1
            continue
        cue_id = item.get("id")
        text = item.get("text")
        if not isinstance(cue_id, int) or isinstance(cue_id, bool) or not isinstance(text, str):
            invalid_count += 1
            continue
        if cue_id not in expected_ids:
            unexpected_count += 1
            continue
        if cue_id in accepted_ids:
            duplicate_count += 1
            continue
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        cleaned = re.sub(r"\n[ \t]*\n+", "\n", cleaned)
        if not cleaned:
            original_text = original_text_by_id[cue_id]
            if original_text.strip():
                blank_nonempty_ids.add(cue_id)
                continue
            accepted_ids.add(cue_id)
            improvements[cue_id] = ""
            continue
        improvements[cue_id] = cleaned
        accepted_ids.add(cue_id)

    missing_ids = expected_ids - accepted_ids
    return NormalizationReport(
        improvements=improvements,
        accepted_ids=frozenset(accepted_ids),
        missing_ids=frozenset(missing_ids),
        blank_nonempty_ids=frozenset(blank_nonempty_ids),
        unexpected_count=unexpected_count,
        duplicate_count=duplicate_count,
        invalid_count=invalid_count,
    )


def report_notes(report: NormalizationReport) -> list[str]:
    notes: list[str] = []
    if report.missing_ids:
        notes.append(f"kept original text for {len(report.missing_ids)} missing/blank cue(s)")
    if report.unexpected_count:
        notes.append(f"ignored {report.unexpected_count} extra context cue(s)")
    if report.duplicate_count:
        notes.append(f"ignored {report.duplicate_count} duplicate cue(s)")
    if report.invalid_count:
        notes.append(f"ignored {report.invalid_count} invalid cue entry/entries")
    return notes


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SubtitleError(f"Could not fingerprint subtitle file: {path}") from exc
    return digest.hexdigest()


def checkpoint_signature(
    reference_path: Path,
    target_path: Path,
    target_language: str,
    model: str,
    chunk_size: int,
    target_count: int,
) -> dict[str, Any]:
    return {
        "version": 1,
        "reference_sha256": file_sha256(reference_path),
        "target_sha256": file_sha256(target_path),
        "target_language": target_language,
        "model": model,
        "chunk_size": chunk_size,
        "target_count": target_count,
    }


def atomic_write_text(path: Path, content: str) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(content, encoding="utf-8")
        os.replace(temporary_path, path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SubtitleError(f"Could not write file atomically: {path}") from exc


def save_checkpoint(
    path: Path,
    signature: dict[str, Any],
    completed_until: int,
    improvements: dict[int, str],
) -> None:
    completed = {str(cue_id): improvements[cue_id] for cue_id in range(completed_until)}
    document = {
        "signature": signature,
        "completed_until": completed_until,
        "improvements": completed,
    }
    atomic_write_text(path, json.dumps(document, ensure_ascii=False, separators=(",", ":")))


def load_checkpoint(
    path: Path,
    signature: dict[str, Any],
    target_count: int,
) -> tuple[dict[int, str], int]:
    if not path.exists():
        return {}, 0
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("signature") != signature:
            raise ValueError("signature mismatch")
        completed_until = document.get("completed_until")
        raw_improvements = document.get("improvements")
        if (
            not isinstance(completed_until, int)
            or isinstance(completed_until, bool)
            or completed_until < 0
            or completed_until > target_count
            or not isinstance(raw_improvements, dict)
            or set(raw_improvements) != {str(cue_id) for cue_id in range(completed_until)}
            or not all(isinstance(value, str) for value in raw_improvements.values())
        ):
            raise ValueError("invalid checkpoint content")
        improvements = {int(cue_id): text for cue_id, text in raw_improvements.items()}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        print(f"Ignoring an incompatible or damaged checkpoint: {path}", file=sys.stderr)
        return {}, 0
    if completed_until:
        print(
            f"Resuming from checkpoint at subtitle cue {completed_until + 1} of {target_count}.",
            file=sys.stderr,
        )
    return improvements, completed_until


def write_srt(path: Path, target: list[Cue], improved_text: dict[int, str]) -> None:
    blocks = []
    for cue in target:
        text = improved_text.get(cue.position, cue.text)
        blocks.append(f"{cue.number}\n{cue.timing_line}\n{text}")
    atomic_write_text(path, "\n\n".join(blocks) + "\n")


def improve_subtitle(
    reference_path: Path,
    target_path: Path,
    output_path: Path,
    target_language: str,
    model: str,
    api_url: str,
    api_key: str,
    chunk_size: int,
) -> None:
    reference = parse_srt(reference_path)
    target = parse_srt(target_path)
    if not reference:
        raise SubtitleError("The English reference subtitle contains no cues.")
    if not target:
        raise SubtitleError("The target subtitle contains no cues.")

    total = len(target)
    signature = checkpoint_signature(
        reference_path,
        target_path,
        target_language,
        model,
        chunk_size,
        total,
    )
    checkpoint_path = output_path.with_name(output_path.name + ".checkpoint.json")
    improved_text, completed_until = load_checkpoint(checkpoint_path, signature, total)

    for start_index in range(completed_until, total, chunk_size):
        end_index = min(total, start_index + chunk_size)
        print(f"Improving subtitle cues {start_index + 1}-{end_index} of {total}...", file=sys.stderr)
        payload, expected_ids = build_payload(
            model,
            target_language,
            reference,
            target,
            start_index,
            end_index,
        )
        original_text_by_id = {cue.position: cue.text for cue in target[start_index:end_index]}
        best_report: NormalizationReport | None = None
        last_output_error: ChunkOutputError | None = None
        for output_attempt in range(1, 3):
            try:
                response = call_responses_api(api_url, api_key, payload)
                result = extract_structured_output(response)
                report = validate_improvements(result, expected_ids, original_text_by_id)
                if best_report is None or report.accepted_count > best_report.accepted_count:
                    best_report = report
                # A few missing cues can safely retain their originals. Retry
                # only when the response missed more than 20% of the chunk.
                if report.accepted_count * 5 >= len(expected_ids) * 4:
                    break
                last_output_error = ChunkOutputError(
                    f"only {report.accepted_count} of {len(expected_ids)} requested cues were usable"
                )
            except ChunkOutputError as exc:
                last_output_error = exc
            if output_attempt == 1:
                print(
                    f"OpenAI returned an incomplete chunk ({last_output_error}); retrying once...",
                    file=sys.stderr,
                )

        if best_report is None:
            best_report = NormalizationReport(
                improvements=dict(original_text_by_id),
                accepted_ids=frozenset(),
                missing_ids=frozenset(expected_ids),
                blank_nonempty_ids=frozenset(),
                unexpected_count=0,
                duplicate_count=0,
                invalid_count=0,
            )
        if best_report.accepted_count * 5 < len(expected_ids) * 4:
            print(
                f"Warning: chunk {start_index + 1}-{end_index} remained incomplete after retry; "
                "original text was retained for unusable cues.",
                file=sys.stderr,
            )
        notes = report_notes(best_report)
        if notes:
            print("Chunk note: " + "; ".join(notes) + ".", file=sys.stderr)
        improved_text.update(best_report.improvements)
        save_checkpoint(checkpoint_path, signature, end_index, improved_text)

    if set(improved_text) != {cue.position for cue in target}:
        raise SubtitleError("Not every target subtitle cue was improved.")
    write_srt(output_path, target, improved_text)

    # Parse the generated file and prove that cue count and timing stayed unchanged.
    generated = parse_srt(output_path)
    if len(generated) != len(target):
        output_path.unlink(missing_ok=True)
        raise SubtitleError("Generated subtitle cue count changed unexpectedly.")
    for before, after in zip(target, generated):
        if (before.number, before.timing_line) != (after.number, after.timing_line):
            output_path.unlink(missing_ok=True)
            raise SubtitleError("Generated subtitle timing or numbering changed unexpectedly.")
    try:
        checkpoint_path.unlink(missing_ok=True)
    except OSError:
        print(f"Warning: could not remove completed checkpoint: {checkpoint_path}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Improve a target-language SRT using English subtitles as reference."
    )
    parser.add_argument("--reference", required=True, type=Path, help="English reference SRT")
    parser.add_argument("--target", required=True, type=Path, help="Target-language SRT")
    parser.add_argument("--output", required=True, type=Path, help="Improved output SRT")
    parser.add_argument("--language", required=True, help="Target language name and/or code")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_SUBTITLE_MODEL", DEFAULT_MODEL),
        help=f"OpenAI model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY is not set in this shell.", file=sys.stderr)
        return 2
    if args.chunk_size < 1 or args.chunk_size > 200:
        print("Chunk size must be between 1 and 200.", file=sys.stderr)
        return 2
    api_url = os.environ.get("OPENAI_API_URL", DEFAULT_API_URL).strip() or DEFAULT_API_URL
    try:
        improve_subtitle(
            args.reference,
            args.target,
            args.output,
            args.language,
            args.model,
            api_url,
            api_key,
            args.chunk_size,
        )
    except SubtitleError as exc:
        print(f"Subtitle improvement failed: {exc}", file=sys.stderr)
        return 1
    print(f"Improved subtitle saved to: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
