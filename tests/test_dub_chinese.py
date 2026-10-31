from __future__ import annotations

import io
import json
import os
import math
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

import dub_chinese  # noqa: E402
from dub_chinese import Segment, bilingual_cues, build_segments, clean_text, plan_timeline, tidy_cues  # noqa: E402
from subtitle_improver import Cue  # noqa: E402


def cue(start: int, end: int, text: str) -> Cue:
    return Cue(0, "1", "", start, end, text)


def tone_wav(seconds: float, rate: int = 32_000, lead_silence: float = 0.2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        silent = int(rate * lead_silence)
        for index in range(int(rate * seconds)):
            value = 0 if index < silent else int(9000 * math.sin(2 * math.pi * 220 * index / rate))
            frames += struct.pack("<h", value)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


class FakeTTSHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    fail_text = "无法合成"
    broken = False

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def do_GET(self) -> None:
        self.send_response(400)
        self.end_headers()
        self.wfile.write(b'{"message": "text is required"}')

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeTTSHandler.requests.append(payload)
        if FakeTTSHandler.broken:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"message": "tts failed", "Exception": "No module named resampy"}')
            return
        if payload["text"] == self.fail_text:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"message": "bad text"}')
            return
        body = tone_wav(0.25 * len(payload["text"]))  # long text -> long audio
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TextAndTimingTests(unittest.TestCase):
    def test_clean_text_removes_tags_and_annotations(self) -> None:
        self.assertEqual(clean_text("<i>大家好</i> [音乐]\n今天 我们"), "大家好今天我们")
        self.assertEqual(clean_text("[Music]"), "")
        self.assertEqual(clean_text("使用 SQL 查询"), "使用SQL查询")

    def test_build_segments_merges_unfinished_sentences(self) -> None:
        segments = build_segments(
            [
                cue(0, 1000, "今天我们"),
                cue(1100, 2000, "学习网络。"),
                cue(2100, 3000, "第二句"),
                cue(5000, 6000, "很远的一句。"),
            ]
        )
        self.assertEqual([s.text for s in segments], ["今天我们学习网络。", "第二句", "很远的一句。"])
        self.assertEqual((segments[0].start_ms, segments[0].end_ms), (0, 2000))

    def test_build_segments_skips_untranslated_english_cues(self) -> None:
        segments = build_segments(
            [
                cue(0, 1000, "民调显示了什么？"),
                cue(1000, 2000, "President Trump as he rallies"),
                cue(2000, 3000, "So,"),
                cue(3000, 4000, "使用 SQL 查询。"),
            ]
        )
        self.assertEqual([s.text for s in segments], ["民调显示了什么？", "使用SQL查询。"])

    def test_merging_does_not_add_stray_commas(self) -> None:
        segments = build_segments(
            [cue(0, 1000, "至少目前的民调——"), cue(1100, 2000, "记住，这只是快照"), cue(2100, 3000, "的结果。")]
        )
        self.assertEqual(segments[0].text, "至少目前的民调——记住，这只是快照的结果。")

    def test_build_segments_drops_repeated_rolling_captions_and_overlaps(self) -> None:
        segments = build_segments(
            [cue(0, 3000, "第一句。"), cue(1500, 3500, "第一句。"), cue(2500, 4000, "第二句。")]
        )
        self.assertEqual([s.text for s in segments], ["第一句。", "第二句。"])
        self.assertEqual(segments[0].end_ms, 2500)

    def test_plan_timeline_speeds_up_and_carries_overflow(self) -> None:
        segments = [Segment(0, 0, 1000, "a"), Segment(1, 1000, 2000, "b"), Segment(2, 5000, 6000, "c")]
        placements = plan_timeline(segments, [1840, 500, 500], 7000, max_speed=1.35)
        self.assertEqual(placements[0].start_ms, 0)
        self.assertAlmostEqual(placements[0].tempo, 1.35)
        # 1840/1.35 = 1363 ms + gap, so the second clip starts late.
        self.assertEqual(placements[1].start_ms, 1443)
        # The pause before segment 3 absorbs the delay.
        self.assertEqual(placements[2].start_ms, 5000)
        self.assertEqual(placements[2].tempo, 1.0)


class SubtitleTrackTests(unittest.TestCase):
    def test_tidy_cues_removes_rolling_duplicates_and_overlaps(self) -> None:
        tidy = tidy_cues([cue(0, 3000, "Hello"), cue(1000, 3500, "Hello"), cue(3000, 5000, "<i>world</i>"), cue(4000, 6000, " ")])
        self.assertEqual(tidy, [(0, 3000, "Hello"), (3000, 5000, "world")])

    def test_bilingual_pairs_by_largest_overlap(self) -> None:
        chinese = [(0, 2000, "你好"), (2000, 4000, "世界"), (5000, 6000, "无对应")]
        english = [(0, 1500, "Hello"), (1500, 3800, "the\nworld")]
        self.assertEqual(
            bilingual_cues(chinese, english),
            [(0, 2000, "你好\nHello"), (2000, 4000, "世界\nthe world"), (5000, 6000, "无对应")],
        )


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTTSHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.api_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def make_inputs(self, directory: Path) -> tuple[Path, Path, Path]:
        video = directory / "lecture.mp4"
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=8",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video),
            ],
            check=True,
        )
        srt = directory / "lecture.zh.srt"
        srt.write_text(
            "1\n00:00:00,500 --> 00:00:02,000\n大家好，\n\n"
            "2\n00:00:02,100 --> 00:00:03,500\n今天学习网络。\n\n"
            "3\n00:00:04,000 --> 00:00:05,000\n无法合成\n\n"
            "4\n00:00:05,500 --> 00:00:07,000\n[音乐]\n\n"
            "5\n00:00:06,000 --> 00:00:07,500\n谢谢。\n",
            encoding="utf-8",
        )
        voice = directory / "voices" / "test-voice"
        voice.mkdir(parents=True)
        (voice / "ref.wav").write_bytes(tone_wav(6))
        (voice / "voice.json").write_text(
            json.dumps({"name": "test-voice", "ref_audio": "ref.wav", "prompt_text": "参考音频。", "prompt_lang": "zh"}),
            encoding="utf-8",
        )
        return video, srt, voice

    def ffprobe_streams(self, path: Path) -> list[dict]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)["streams"]

    def test_creates_dual_audio_mp4_and_resumes_from_cache(self) -> None:
        FakeTTSHandler.requests = []
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            video, srt, voice = self.make_inputs(directory)
            output = directory / "lecture [zh-dub].mp4"
            arguments = [
                "--video", str(video), "--srt", str(srt), "--voice", str(voice),
                "--output", str(output), "--workdir", str(directory / "work"), "--api-url", self.api_url,
            ]
            self.assertEqual(dub_chinese.main(arguments), 0)

            texts = [request["text"] for request in FakeTTSHandler.requests]
            self.assertEqual(texts, ["大家好，今天学习网络。", "无法合成", "谢谢。"])
            first = FakeTTSHandler.requests[0]
            self.assertEqual(first["text_lang"], "zh")
            self.assertEqual(first["prompt_text"], "参考音频。")
            self.assertTrue(Path(first["ref_audio_path"]).is_absolute())

            streams = self.ffprobe_streams(output)
            audio = [stream for stream in streams if stream["codec_type"] == "audio"]
            subtitles = [stream for stream in streams if stream["codec_type"] == "subtitle"]
            self.assertEqual(len(audio), 2)
            self.assertEqual(audio[0]["tags"]["language"], "chi")
            self.assertEqual(audio[0]["disposition"]["default"], 1)
            self.assertEqual(audio[1]["tags"]["language"], "eng")
            self.assertEqual(audio[1]["disposition"]["default"], 0)
            self.assertEqual(len(subtitles), 1)

            # Second run reuses every finished line and only retries the rejected one.
            FakeTTSHandler.requests = []
            arguments[arguments.index(str(output))] = str(directory / "again.mp4")
            self.assertEqual(dub_chinese.main(arguments), 0)
            self.assertEqual([request["text"] for request in FakeTTSHandler.requests], ["无法合成"])

    def test_adds_english_and_bilingual_subtitle_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            video, srt, voice = self.make_inputs(directory)
            english = directory / "lecture.en.srt"
            english.write_text(
                "1\n00:00:00,500 --> 00:00:03,500\nHello everyone, today we learn networking.\n\n"
                "2\n00:00:06,000 --> 00:00:07,500\nThank you.\n",
                encoding="utf-8",
            )
            output = directory / "subs.mp4"
            code = dub_chinese.main([
                "--video", str(video), "--srt", str(srt), "--voice", str(voice), "--output", str(output),
                "--workdir", str(directory / "work"), "--api-url", self.api_url, "--english-srt", str(english),
            ])
            self.assertEqual(code, 0)
            subtitles = [st for st in self.ffprobe_streams(output) if st["codec_type"] == "subtitle"]
            self.assertEqual([st["tags"]["language"] for st in subtitles], ["chi", "eng", "chi"])
            self.assertEqual(subtitles[2]["tags"]["handler_name"], "中英双语")
            bilingual = (directory / "work" / "subtitle.zh-en.srt").read_text(encoding="utf-8")
            self.assertIn("谢谢。\nThank you.", bilingual)

    def test_stops_when_the_server_fails_every_line_and_resumes_later(self) -> None:
        FakeTTSHandler.requests = []
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            video, srt, voice = self.make_inputs(directory)
            more = "".join(
                f"{n}\n00:00:0{n},000 --> 00:00:0{n},500\n第{n}句。\n\n" for n in range(1, 6)
            )
            srt.write_text(more, encoding="utf-8")
            arguments = [
                "--video", str(video), "--srt", str(srt), "--voice", str(voice),
                "--output", str(directory / "out.mp4"), "--workdir", str(directory / "work"),
                "--api-url", self.api_url,
            ]
            FakeTTSHandler.broken = True
            try:
                self.assertEqual(dub_chinese.main(arguments), 3)
            finally:
                FakeTTSHandler.broken = False
            self.assertEqual(len(FakeTTSHandler.requests), 3)
            self.assertFalse((directory / "out.mp4").exists())

            FakeTTSHandler.requests = []
            self.assertEqual(dub_chinese.main(arguments), 0)
            self.assertEqual(len(FakeTTSHandler.requests), 5)

    def test_keeps_background_with_demucs(self) -> None:
        FakeTTSHandler.requests = []
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            video, srt, voice = self.make_inputs(directory)
            # A stand-in "demucs" package: writes a quiet 300 Hz tone as no_vocals.wav.
            package = directory / "fake_modules" / "demucs"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "__main__.py").write_text(
                "import sys, subprocess\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "out = Path(args[args.index('-o') + 1]) / 'htdemucs'\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',\n"
                "    'sine=frequency=300:duration=8', '-ac', '2', str(out / 'no_vocals.wav')], check=True)\n",
                encoding="utf-8",
            )
            output = directory / "with-bg.mp4"
            work = directory / "work"
            arguments = [
                "--video", str(video), "--srt", str(srt), "--voice", str(voice), "--output", str(output),
                "--workdir", str(work), "--api-url", self.api_url, "--keep-background",
                "--demucs-python", sys.executable,
            ]
            old_path = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = str(directory / "fake_modules")
            try:
                self.assertEqual(dub_chinese.main(arguments), 0)
            finally:
                if old_path is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = old_path

            self.assertTrue((work / "background.wav").is_file())
            with wave.open(str(work / "chinese_mix.wav"), "rb") as mixed:
                self.assertEqual(mixed.getnchannels(), 2)
                rate = mixed.getframerate()
                mixed.setpos(int(4.3 * rate))  # the rejected line: no voice here, only background
                frames = mixed.readframes(int(0.2 * rate))
            samples = struct.unpack(f"<{len(frames) // 2}h", frames)
            self.assertGreater(max(abs(value) for value in samples), 1000)
            audio = [st for st in self.ffprobe_streams(output) if st["codec_type"] == "audio"]
            self.assertIn("背景音", audio[0]["tags"].get("title") or audio[0]["tags"].get("handler_name", ""))

    def test_keep_background_explains_missing_demucs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            video, srt, voice = self.make_inputs(directory)
            code = dub_chinese.main([
                "--video", str(video), "--srt", str(srt), "--voice", str(voice),
                "--output", str(directory / "x.mp4"), "--workdir", str(directory / "w"),
                "--api-url", self.api_url, "--keep-background", "--demucs-python", sys.executable,
            ])
            self.assertEqual(code, 1)

    def test_refuses_to_overwrite_and_reports_unreachable_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            video, srt, voice = self.make_inputs(directory)
            existing = directory / "exists.mp4"
            existing.write_bytes(b"keep")
            common = ["--video", str(video), "--srt", str(srt), "--voice", str(voice), "--workdir", str(directory / "w")]
            self.assertEqual(dub_chinese.main(common + ["--output", str(existing), "--api-url", self.api_url]), 1)
            self.assertEqual(existing.read_bytes(), b"keep")
            code = dub_chinese.main(common + ["--output", str(directory / "new.mp4"), "--api-url", "http://127.0.0.1:9"])
            self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
