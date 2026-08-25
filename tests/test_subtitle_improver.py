from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPOSITORY_ROOT))

import subtitle_improver as improver


ENGLISH_SRT = (FIXTURE_DIRECTORY / "english.srt").read_text(encoding="utf-8")
TARGET_SRT = (FIXTURE_DIRECTORY / "chinese.srt").read_text(encoding="utf-8")
TARGET_WITH_BLANK_CUE = """1
00:00:00,000 --> 00:00:02,000
Translated greeting

2
00:00:02,000 --> 00:00:03,000

3
00:00:03,000 --> 00:00:06,000
Translated sentence
"""


class MockResponsesHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    echo_target_text = False
    response_plan: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            }
        )
        mode = self.__class__.response_plan.pop(0) if self.__class__.response_plan else "normal"
        if mode == "http_400":
            encoded = json.dumps(
                {"error": {"message": "synthetic terminal API error"}}
            ).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        document = json.loads(payload["input"])
        editable_ids = [cue["id"] for cue in document["editable_target_cues"]]
        target_by_id = {cue["id"]: cue["text"] for cue in document["editable_target_cues"]}
        result = {"cues": []}
        output_ids = editable_ids[:1] if mode == "missing" else editable_ids
        for cue_id in output_ids:
            if self.__class__.echo_target_text:
                text = target_by_id[cue_id]
            else:
                text = f"改进的字幕 {cue_id + 1}"
            result["cues"].append({"id": cue_id, "text": text})
        if mode == "extra_context":
            result["cues"].append({"id": max(editable_ids) + 1, "text": ""})
        output_text = (
            "this is not JSON"
            if mode == "malformed"
            else json.dumps(result, ensure_ascii=False)
        )
        response = {
            "id": "resp_test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": output_text,
                        }
                    ],
                }
            ],
        }
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


class SubtitleImproverTests(unittest.TestCase):
    def setUp(self) -> None:
        MockResponsesHandler.requests = []
        MockResponsesHandler.echo_target_text = False
        MockResponsesHandler.response_plan = []

    def run_improver_cli(
        self,
        reference_path: Path,
        target_path: Path,
        output_path: Path,
        port: int,
        chunk_size: int,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_API_URL": f"http://127.0.0.1:{port}/v1/responses",
            }
        )
        return subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "subtitle_improver.py"),
                "--reference",
                str(reference_path),
                "--target",
                str(target_path),
                "--output",
                str(output_path),
                "--language",
                "Simplified Chinese (zh-Hans)",
                "--model",
                "test-model",
                "--chunk-size",
                str(chunk_size),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_parse_srt_accepts_different_reference_and_target_cue_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            reference_path = directory / "english.srt"
            target_path = directory / "target.srt"
            reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
            target_path.write_text(TARGET_SRT, encoding="utf-8")

            reference = improver.parse_srt(reference_path)
            target = improver.parse_srt(target_path)

            self.assertEqual(len(reference), 2)
            self.assertEqual(len(target), 3)
            self.assertEqual(target[1].number, "20")
            self.assertEqual(target[1].start_ms, 1_500)
            self.assertEqual(target[1].end_ms, 3_500)

    def test_end_to_end_mock_api_preserves_target_structure(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                reference_path = directory / "english.srt"
                target_path = directory / "target.srt"
                output_path = directory / "improved.srt"
                reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
                target_path.write_text(TARGET_SRT, encoding="utf-8")
                before = improver.parse_srt(target_path)

                environment = os.environ.copy()
                environment.update(
                    {
                        "OPENAI_API_KEY": "test-key",
                        "OPENAI_API_URL": (
                            f"http://127.0.0.1:{server.server_address[1]}/v1/responses"
                        ),
                    }
                )
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(REPOSITORY_ROOT / "subtitle_improver.py"),
                        "--reference",
                        str(reference_path),
                        "--target",
                        str(target_path),
                        "--output",
                        str(output_path),
                        "--language",
                        "Simplified Chinese (zh-Hans)",
                        "--model",
                        "test-model",
                        "--chunk-size",
                        "2",
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                after = improver.parse_srt(output_path)
                self.assertEqual(
                    [(cue.number, cue.timing_line) for cue in after],
                    [(cue.number, cue.timing_line) for cue in before],
                )
                self.assertEqual(
                    [cue.text for cue in after],
                    ["改进的字幕 1", "改进的字幕 2", "改进的字幕 3"],
                )

            self.assertEqual(len(MockResponsesHandler.requests), 2)
            first_request = MockResponsesHandler.requests[0]
            self.assertEqual(first_request["path"], "/v1/responses")
            self.assertEqual(first_request["authorization"], "Bearer test-key")
            payload = first_request["payload"]
            self.assertEqual(payload["model"], "test-model")
            self.assertIs(payload["store"], False)
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertIs(payload["text"]["format"]["strict"], True)
            document = json.loads(payload["input"])
            self.assertEqual(document["target_language"], "Simplified Chinese (zh-Hans)")
            self.assertEqual(len(document["english_reference_cues"]), 2)
            self.assertEqual(
                [cue["id"] for cue in document["editable_target_cues"]],
                [0, 1],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_end_to_end_accepts_blank_timing_placeholder_from_model(self) -> None:
        MockResponsesHandler.echo_target_text = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                reference_path = directory / "english.srt"
                target_path = directory / "target.srt"
                output_path = directory / "improved.srt"
                reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
                target_path.write_text(TARGET_WITH_BLANK_CUE, encoding="utf-8")
                environment = os.environ.copy()
                environment.update(
                    {
                        "OPENAI_API_KEY": "test-key",
                        "OPENAI_API_URL": (
                            f"http://127.0.0.1:{server.server_address[1]}/v1/responses"
                        ),
                    }
                )

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(REPOSITORY_ROOT / "subtitle_improver.py"),
                        "--reference",
                        str(reference_path),
                        "--target",
                        str(target_path),
                        "--output",
                        str(output_path),
                        "--language",
                        "Uyghur (ug)",
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                generated = improver.parse_srt(output_path)
                self.assertEqual(len(generated), 3)
                self.assertEqual(generated[1].text, "")
                self.assertEqual(generated[1].timing_line, "00:00:02,000 --> 00:00:03,000")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_extra_context_cue_is_ignored_without_retrying_or_failing(self) -> None:
        MockResponsesHandler.response_plan = ["extra_context"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                reference_path = directory / "english.srt"
                target_path = directory / "target.srt"
                output_path = directory / "improved.srt"
                reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
                target_path.write_text(TARGET_SRT, encoding="utf-8")

                completed = self.run_improver_cli(
                    reference_path,
                    target_path,
                    output_path,
                    server.server_address[1],
                    chunk_size=3,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(len(MockResponsesHandler.requests), 1)
                self.assertIn("ignored 1 extra context cue", completed.stderr)
                self.assertEqual(len(improver.parse_srt(output_path)), 3)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_malformed_chunk_retries_once_then_succeeds(self) -> None:
        MockResponsesHandler.response_plan = ["malformed", "normal"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                reference_path = directory / "english.srt"
                target_path = directory / "target.srt"
                output_path = directory / "improved.srt"
                reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
                target_path.write_text(TARGET_SRT, encoding="utf-8")

                completed = self.run_improver_cli(
                    reference_path,
                    target_path,
                    output_path,
                    server.server_address[1],
                    chunk_size=3,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(len(MockResponsesHandler.requests), 2)
                self.assertIn("retrying once", completed.stderr)
                self.assertTrue(output_path.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_incomplete_chunk_keeps_originals_after_retry(self) -> None:
        MockResponsesHandler.response_plan = ["missing", "missing"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                reference_path = directory / "english.srt"
                target_path = directory / "target.srt"
                output_path = directory / "improved.srt"
                reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
                target_path.write_text(TARGET_SRT, encoding="utf-8")

                completed = self.run_improver_cli(
                    reference_path,
                    target_path,
                    output_path,
                    server.server_address[1],
                    chunk_size=3,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(len(MockResponsesHandler.requests), 2)
                generated = improver.parse_srt(output_path)
                original = improver.parse_srt(target_path)
                self.assertEqual(generated[0].text, "改进的字幕 1")
                self.assertEqual(generated[1].text, original[1].text)
                self.assertEqual(generated[2].text, original[2].text)
                self.assertIn("remained incomplete after retry", completed.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_terminal_api_failure_checkpoints_and_next_run_resumes(self) -> None:
        MockResponsesHandler.response_plan = ["normal", "http_400"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                reference_path = directory / "english.srt"
                target_path = directory / "target.srt"
                output_path = directory / "improved.srt"
                checkpoint_path = directory / "improved.srt.checkpoint.json"
                reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
                target_path.write_text(TARGET_SRT, encoding="utf-8")

                first = self.run_improver_cli(
                    reference_path,
                    target_path,
                    output_path,
                    server.server_address[1],
                    chunk_size=2,
                )

                self.assertEqual(first.returncode, 1)
                self.assertFalse(output_path.exists())
                self.assertTrue(checkpoint_path.exists())
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                self.assertEqual(checkpoint["completed_until"], 2)

                MockResponsesHandler.response_plan = ["normal"]
                second = self.run_improver_cli(
                    reference_path,
                    target_path,
                    output_path,
                    server.server_address[1],
                    chunk_size=2,
                )

                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn("Resuming from checkpoint at subtitle cue 3", second.stderr)
                self.assertEqual(len(MockResponsesHandler.requests), 3)
                self.assertFalse(checkpoint_path.exists())
                generated = improver.parse_srt(output_path)
                self.assertEqual(
                    [cue.text for cue in generated],
                    ["改进的字幕 1", "改进的字幕 2", "改进的字幕 3"],
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_bash_dual_subtitle_hook_uses_english_and_saves_improved_srt(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                work_directory = directory / "work"
                output_directory = directory / "output"
                work_directory.mkdir()
                output_directory.mkdir()
                (work_directory / "language1.srt").write_text(ENGLISH_SRT, encoding="utf-8")
                (work_directory / "language2.srt").write_text(TARGET_SRT, encoding="utf-8")

                environment = os.environ.copy()
                environment.update(
                    {
                        "OPENAI_API_KEY": "test-key",
                        "OPENAI_API_URL": (
                            f"http://127.0.0.1:{server.server_address[1]}/v1/responses"
                        ),
                        "OPENAI_SUBTITLE_MODEL": "test-model",
                        "TEST_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                        "TEST_WORK_DIRECTORY": str(work_directory),
                        "TEST_OUTPUT_DIRECTORY": str(output_directory),
                    }
                )
                bash_program = r'''
source "$TEST_REPOSITORY_ROOT/ytgrab.sh"
OUTDIR="$TEST_OUTPUT_DIRECTORY"
DUAL_WORKDIR="$TEST_WORK_DIRECTORY"
OUTPUT_BASE="Fixture Video"
TRACK1_CODE="en"
TRACK2_CODE="zh-Hans"
LANG1_LABEL="English"
LANG2_LABEL="Simplified Chinese"
ask_yes_no() { return 0; }
maybe_improve_subtitle_with_openai
'''
                completed = subprocess.run(
                    ["bash", "-c", bash_program],
                    cwd=REPOSITORY_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                active_target = improver.parse_srt(work_directory / "language2.srt")
                self.assertEqual(
                    [cue.text for cue in active_target],
                    ["改进的字幕 1", "改进的字幕 2", "改进的字幕 3"],
                )
                saved_outputs = list(output_directory.glob("*.srt"))
                self.assertEqual(len(saved_outputs), 1)
                self.assertIn("OpenAI-improved-zh-Hans", saved_outputs[0].name)
                self.assertEqual(
                    saved_outputs[0].read_text(encoding="utf-8"),
                    (work_directory / "language2.srt").read_text(encoding="utf-8"),
                )
                self.assertTrue((work_directory / "target-before-openai.srt").exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_validate_improvements_tolerates_missing_and_extra_cues(self) -> None:
        report = improver.validate_improvements(
            {"cues": [{"id": 1, "text": "improved"}, {"id": 9, "text": "extra"}]},
            {1, 2},
            {1: "original one", 2: "original two"},
        )

        self.assertEqual(report.improvements, {1: "improved", 2: "original two"})
        self.assertEqual(report.accepted_ids, {1})
        self.assertEqual(report.missing_ids, {2})
        self.assertEqual(report.unexpected_count, 1)

    def test_validate_improvements_ignores_duplicate_and_invalid_entries(self) -> None:
        report = improver.validate_improvements(
            {
                "cues": [
                    {"id": 1, "text": "first valid edit"},
                    {"id": 1, "text": "duplicate edit"},
                    {"id": "2", "text": "invalid ID"},
                    "not an object",
                ]
            },
            {1, 2},
            {1: "original one", 2: "original two"},
        )

        self.assertEqual(
            report.improvements,
            {1: "first valid edit", 2: "original two"},
        )
        self.assertEqual(report.duplicate_count, 1)
        self.assertEqual(report.invalid_count, 2)
        self.assertEqual(report.missing_ids, {2})

    def test_validate_improvements_accepts_an_existing_blank_placeholder(self) -> None:
        report = improver.validate_improvements(
            {"cues": [{"id": 4, "text": ""}]},
            {4},
            {4: ""},
        )

        self.assertEqual(report.improvements, {4: ""})
        self.assertEqual(report.accepted_ids, {4})

    def test_validate_improvements_keeps_nonempty_original_if_model_returns_blank(self) -> None:
        report = improver.validate_improvements(
            {"cues": [{"id": 7, "text": "   "}]},
            {7},
            {7: "original subtitle"},
        )

        self.assertEqual(report.improvements, {7: "original subtitle"})
        self.assertEqual(report.blank_nonempty_ids, {7})
        self.assertEqual(report.missing_ids, {7})

    def test_main_does_not_create_output_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            reference_path = directory / "english.srt"
            target_path = directory / "target.srt"
            output_path = directory / "improved.srt"
            reference_path.write_text(ENGLISH_SRT, encoding="utf-8")
            target_path.write_text(TARGET_SRT, encoding="utf-8")
            environment = os.environ.copy()
            environment.pop("OPENAI_API_KEY", None)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "subtitle_improver.py"),
                    "--reference",
                    str(reference_path),
                    "--target",
                    str(target_path),
                    "--output",
                    str(output_path),
                    "--language",
                    "Uyghur (ug)",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("OPENAI_API_KEY is not set", completed.stderr)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
