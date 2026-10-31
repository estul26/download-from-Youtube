from __future__ import annotations

import os
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


if __name__ == "__main__":
    unittest.main()
