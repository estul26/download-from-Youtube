from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DubMenuTests(unittest.TestCase):
    def run_bash(self, program: str, directory: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
        home = directory / "home"
        home.mkdir(exist_ok=True)
        environment = os.environ.copy()
        environment.update({"HOME": str(home), "TEST_SCRIPT_DIR": str(directory / "app")})
        environment.pop("GPT_SOVITS_API_URL", None)
        return subprocess.run(
            ["bash", "-c", f'source "{REPOSITORY_ROOT}/ytgrab.sh"; SCRIPT_DIR="$TEST_SCRIPT_DIR"; {program}'],
            cwd=REPOSITORY_ROOT, env=environment, input=stdin, text=True, capture_output=True, check=False,
        )

    def make_voice(self, root: Path, name: str) -> None:
        voice = root / "app" / "voices" / name
        voice.mkdir(parents=True)
        (voice / "voice.json").write_text("{}", encoding="utf-8")

    def test_lists_saved_voices_and_skips_working_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ("alpha", "beta", "_candidates"):
                self.make_voice(directory, name)
            (directory / "app" / "voices" / "no-config").mkdir()
            result = self.run_bash("list_dub_voices", directory)
            names = [Path(line).name for line in result.stdout.split()]
            self.assertEqual(names, ["alpha", "beta"])

    def test_choose_voice_menu_and_missing_voice_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = self.run_bash("choose_dub_voice; echo status=$?", directory)
            self.assertIn("fetch_voices.py", result.stdout)
            self.assertIn("status=1", result.stdout)

            self.make_voice(directory, "alpha")
            self.make_voice(directory, "beta")
            result = self.run_bash('choose_dub_voice; echo "picked=$DUB_VOICE_NAME"', directory, stdin="5\n2\n")
            self.assertIn("Please choose 1 through 2.", result.stdout)
            self.assertIn("picked=beta", result.stdout)

    def test_local_subtitles_are_converted_to_srt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            vtt = directory / "lecture.vtt"
            vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n", encoding="utf-8")
            srt = directory / "lecture.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
            result = self.run_bash(
                f'DUB_WORKDIR="$(mktemp -d)"; local_subtitle_as_srt "{srt}" chinese;'
                f' converted="$(local_subtitle_as_srt "{vtt}" english)"; echo "$converted"; cat "$converted";'
                f' local_subtitle_as_srt "{directory}/x.txt" bad; echo "status=$?"',
                directory,
            )
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], str(srt))
            self.assertTrue(lines[1].endswith("/english.srt"))
            self.assertIn("Hello", result.stdout)
            self.assertIn("status=1", result.stdout)

    def test_server_check_detects_a_stopped_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_bash(
                'GPT_SOVITS_API_URL=http://127.0.0.1:9; gpt_sovits_is_running && echo up || echo down', Path(temporary)
            )
            self.assertEqual(result.stdout.strip(), "down")

    def test_dub_workdir_cleanup_only_removes_safe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = self.run_bash(
                'DUB_WORKDIR="$(mktemp -d "$OUTDIR/ytgrab-dub-work.XXXXXX")"; keep="$OUTDIR/keep"; mkdir "$keep";'
                ' cleanup_dub_workdir; ls "$OUTDIR"; DUB_WORKDIR="$keep"; cleanup_dub_workdir; ls "$OUTDIR"',
                directory,
            )
            self.assertEqual(result.stdout.split(), ["keep", "keep"])


FAKE_API = """
import sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
time.sleep(1)  # pretend to load models
port = int(sys.argv[sys.argv.index("-p") + 1])
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.send_response(400)
        self.end_headers()
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BackgroundChoiceTests(unittest.TestCase):
    def choose(self, answers: str, with_demucs: bool) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "home").mkdir()
            environment = os.environ.copy()
            environment["HOME"] = str(directory / "home")
            environment["GPT_SOVITS_PYTHON"] = subprocess.check_output(["which", "python3"], text=True).strip()
            if with_demucs:
                (directory / "demucs").mkdir()
                (directory / "demucs" / "__init__.py").write_text("", encoding="utf-8")
                environment["PYTHONPATH"] = str(directory)
            result = subprocess.run(
                ["bash", "-c", f'source "{REPOSITORY_ROOT}/ytgrab.sh"; choose_dub_background;'
                 ' echo "keep=$DUB_KEEP_BACKGROUND python=$DUB_DEMUCS_PYTHON"'],
                env=environment, input=answers, text=True, capture_output=True, check=False,
            )
            return result.stdout

    def test_default_yes_keeps_background_when_demucs_is_available(self) -> None:
        output = self.choose("\n", with_demucs=True)
        self.assertIn("keep=1 python=/", output)

    def test_no_means_voice_only(self) -> None:
        self.assertIn("keep=0", self.choose("n\n", with_demucs=True))

    def test_declining_the_install_continues_with_voice_only(self) -> None:
        output = self.choose("y\nn\n", with_demucs=False)
        self.assertIn("Demucs is not installed", output)
        self.assertIn("Continuing with voice only.", output)
        self.assertIn("keep=0", output)


class AutoStartTests(unittest.TestCase):
    def run_with_fake_server(self, program: str, api_source: str = FAKE_API) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            server_dir = directory / "GPT-SoVITS"
            server_dir.mkdir()
            (server_dir / "api_v2.py").write_text(api_source, encoding="utf-8")
            (directory / "home").mkdir()
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(directory / "home"),
                    "TMPDIR": str(directory),
                    "GPT_SOVITS_DIR": str(server_dir),
                    "GPT_SOVITS_PYTHON": subprocess.check_output(["which", "python3"], text=True).strip(),
                    "GPT_SOVITS_API_URL": f"http://127.0.0.1:{free_port()}",
                    "GPT_SOVITS_START_TIMEOUT": "20",
                }
            )
            return subprocess.run(
                ["bash", "-c", f'source "{REPOSITORY_ROOT}/ytgrab.sh"; {program}'],
                env=environment, text=True, capture_output=True, check=False, timeout=60,
            )

    def test_starts_waits_and_stops_the_server_it_started(self) -> None:
        result = self.run_with_fake_server(
            "start_gpt_sovits; pid=$GPT_SOVITS_PID; wait_for_gpt_sovits && echo READY;"
            " stop_gpt_sovits_if_started; kill -0 $pid 2>/dev/null && echo ALIVE || echo GONE;"
            ' gpt_sovits_is_running && echo UP || echo DOWN'
        )
        self.assertIn("READY", result.stdout)
        self.assertIn("GONE", result.stdout)
        self.assertIn("DOWN", result.stdout)

    def test_exit_trap_stops_the_server(self) -> None:
        result = self.run_with_fake_server(
            "start_gpt_sovits; wait_for_gpt_sovits >/dev/null; echo PID=$GPT_SOVITS_PID; exit 0"
        )
        pid = int(result.stdout.split("PID=")[1].split()[0])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_reports_log_when_the_server_crashes(self) -> None:
        result = self.run_with_fake_server(
            'start_gpt_sovits; wait_for_gpt_sovits < /dev/null; echo "status=$?"',
            api_source="raise SystemExit('No module named resampy')",
        )
        self.assertIn("stopped while starting", result.stdout)
        self.assertIn("No module named resampy", result.stdout)
        self.assertIn("status=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
