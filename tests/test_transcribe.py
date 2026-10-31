from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from transcribe import Word, split_into_cues  # noqa: E402

FAKE_FASTER_WHISPER = '''
from types import SimpleNamespace as NS

class WhisperModel:
    def __init__(self, name, device, compute_type):
        assert device == "cpu" and compute_type == "int8"

    def transcribe(self, audio, language, word_timestamps, vad_filter, beam_size):
        assert language == "en" and word_timestamps
        words = [NS(start=0.5, end=0.9, word=" Hello"), NS(start=0.9, end=1.4, word=" everyone."),
                 NS(start=2.0, end=2.4, word=" Today"), NS(start=2.4, end=2.8, word=" we"),
                 NS(start=2.8, end=3.5, word=" learn"), NS(start=3.5, end=4.2, word=" networking.")]
        return iter([NS(end=1.4, words=words[:2]), NS(end=4.2, words=words[2:])]), NS(language="en")
'''


def words(*items: tuple[float, float, str]) -> list[Word]:
    return [Word(start, end, text) for start, end, text in items]


class SplitTests(unittest.TestCase):
    def test_splits_at_sentence_ends_and_pauses(self) -> None:
        cues = split_into_cues(
            words((0, 0.4, " Hi"), (0.4, 0.8, " there."), (1.0, 1.3, " So"), (2.5, 2.9, " next"), (2.9, 3.2, " part"))
        )
        self.assertEqual([text for _, _, text in cues], ["Hi there.", "So", "next part"])
        self.assertEqual(cues[0][:2], (0, 0.8))

    def test_long_runs_break_after_a_late_comma(self) -> None:
        sequence = [(i * 0.5, i * 0.5 + 0.4, f" w{i}") for i in range(13)]
        sequence[9] = (4.5, 4.9, " w9,")
        cues = split_into_cues(words(*sequence))
        self.assertTrue(cues[0][2].endswith("w9,"))
        self.assertEqual(cues[1][2], "w10 w11 w12")


class EndToEndTests(unittest.TestCase):
    def test_writes_srt_with_fake_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "faster_whisper.py").write_text(FAKE_FASTER_WHISPER, encoding="utf-8")
            video = directory / "talk.mp4"
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=5",
                 "-f", "lavfi", "-i", "sine=duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(video)],
                check=True,
            )
            output = directory / "talk.en.srt"
            environment = dict(os.environ, PYTHONPATH=str(directory))
            result = subprocess.run(
                [sys.executable, str(REPOSITORY_ROOT / "transcribe.py"), "--video", str(video), "--output", str(output)],
                env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "1\n00:00:00,500 --> 00:00:01,400\nHello everyone.\n\n"
                "2\n00:00:02,000 --> 00:00:04,200\nToday we learn networking.\n",
            )

    def test_missing_faster_whisper_explains_the_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            video = directory / "talk.mp4"
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", str(video)], check=True
            )
            result = subprocess.run(
                [sys.executable, "-I", str(REPOSITORY_ROOT / "transcribe.py"), "--video", str(video),
                 "--output", str(directory / "out.srt")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("pip install faster-whisper", result.stderr)


if __name__ == "__main__":
    unittest.main()
