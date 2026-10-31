from __future__ import annotations

import io
import json
import math
import struct
import sys
import tarfile
import tempfile
import unittest
import wave
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

import fetch_voices  # noqa: E402
from fetch_voices import parse_speaker_info, parse_transcript_line, score_wav  # noqa: E402


def speech_like_wav(seconds: float, pause_at: float | None = None, pause: float = 0.0, rate: int = 44_100) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(int(rate * seconds)):
            t = index / rate
            silent = t < 0.15 or t > seconds - 0.15
            if pause_at is not None and pause_at <= t < pause_at + pause:
                silent = True
            envelope = 0.6 + 0.4 * math.sin(2 * math.pi * 3 * t)
            value = 0 if silent else int(8000 * envelope * math.sin(2 * math.pi * 200 * t))
            frames += struct.pack("<h", value)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


class ParsingTests(unittest.TestCase):
    def test_transcript_line_drops_pinyin(self) -> None:
        line = "SSB00050001.wav\t广 guang3 州 zhou1 女 nv3 大 da4 学 xue2 生 sheng1"
        self.assertEqual(parse_transcript_line(line), ("SSB00050001", "广州女大学生"))
        self.assertIsNone(parse_transcript_line("# header"))

    def test_speaker_info_handles_comments_and_columns(self) -> None:
        info = parse_speaker_info("# speaker age gender accent\nSSB0005\tA\tfemale\tnorth\nSSB0009 B male south\n")
        self.assertEqual(info["SSB0005"], {"gender": "female", "age_group": "A", "accent": "north"})
        self.assertEqual(info["SSB0009"]["gender"], "male")

    def test_score_prefers_steady_speech_and_rejects_bad_lengths(self) -> None:
        clean = score_wav(speech_like_wav(7.5))
        paused = score_wav(speech_like_wav(7.5, pause_at=3.0, pause=0.45))
        self.assertIsNotNone(clean)
        self.assertIsNotNone(paused)
        self.assertGreater(clean[0], paused[0])
        self.assertIsNone(score_wav(speech_like_wav(7.5, pause_at=3.0, pause=1.0)))
        self.assertIsNone(score_wav(speech_like_wav(3.0)))
        self.assertIsNone(score_wav(speech_like_wav(11.0)))


class ArchiveWorkflowTests(unittest.TestCase):
    def test_scan_filters_female_voices_and_saves_a_voice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive_path = directory / "data_aishell3.tgz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for split in ("train", "test"):
                    add_bytes(archive, f"data_aishell3/{split}/wav/SSB0001/SSB00010001.wav", speech_like_wav(7.0))
                add_bytes(archive, "data_aishell3/train/wav/SSB0002/SSB00020001.wav", speech_like_wav(7.5))
                add_bytes(archive, "data_aishell3/train/wav/SSB0003/SSB00030001.wav", speech_like_wav(8.0))
                add_bytes(archive, "data_aishell3/train/wav/SSB0003/SSB00030002.wav", speech_like_wav(4.0))
                content = (
                    "SSB00010001.wav 你 ni3 好 hao3\n"
                    "SSB00020001.wav 今 jin1 天 tian1 晴 qing2\n"
                    "SSB00030001.wav 下 xia4 雨 yu3\n"
                )
                add_bytes(archive, "data_aishell3/train/content.txt", content.encode("utf-8"))
                add_bytes(
                    archive,
                    "data_aishell3/spk-info.txt",
                    b"SSB0001\tB\tfemale\tnorth\nSSB0002\tC\tfemale\tsouth\nSSB0003\tB\tmale\tnorth\n",
                )

            voices = directory / "voices"
            code = fetch_voices.main(
                ["--voices-dir", str(voices), "--archive", str(archive_path), "--count", "5", "--save-top", "1"]
            )
            self.assertEqual(code, 0)

            candidates = json.loads((voices / "_candidates" / "candidates.json").read_text(encoding="utf-8"))
            self.assertEqual({entry["speaker"] for entry in candidates}, {"SSB0001", "SSB0002"})
            self.assertTrue(all(entry["gender"] == "female" for entry in candidates))

            saved = json.loads((voices / "female-01" / "voice.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["license"], "Apache-2.0")
            self.assertEqual(saved["prompt_lang"], "zh")
            self.assertTrue(saved["prompt_text"].endswith("。"))
            self.assertTrue((voices / "female-01" / "ref.wav").is_file())

            # A second run reuses the scan and keeps existing voices.
            self.assertEqual(fetch_voices.main(["--voices-dir", str(voices), "--save-top", "1"]), 0)
            self.assertTrue((voices / "female-01" / "voice.json").is_file())

    def test_save_voice_rejects_unsafe_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry = {"wav_file": __file__, "text": "你好", "speaker": "SSB1", "utterance": "SSB10001"}
            for name in ("../escape", "_candidates", "bad name"):
                with self.assertRaises(fetch_voices.VoiceError):
                    fetch_voices.save_voice(entry, name, Path(temporary))


if __name__ == "__main__":
    unittest.main()
