from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class WorkdirLifecycleTests(unittest.TestCase):
    def run_bash(
        self, program: str, output_directory: Path, work_directory: Path
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(output_directory.parent / "home"),
                "TEST_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                "TEST_OUTPUT_DIRECTORY": str(output_directory),
                "TEST_WORK_DIRECTORY": str(work_directory),
            }
        )
        Path(environment["HOME"]).mkdir()
        return subprocess.run(
            ["bash", "-c", program],
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_ensure_dual_workdir_recreates_a_removed_safe_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            output_directory = directory / "output"
            output_directory.mkdir()
            work_directory = output_directory / "ytgrab-work.removed"

            completed = self.run_bash(
                r'''
source "$TEST_REPOSITORY_ROOT/ytgrab.sh"
OUTDIR="$TEST_OUTPUT_DIRECTORY"
DUAL_WORKDIR="$TEST_WORK_DIRECTORY"
ensure_dual_workdir
''',
                output_directory,
                work_directory,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(work_directory.is_dir())
            self.assertIn("had been removed", completed.stdout)

    def test_cleanup_removes_a_metadata_only_failure_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            output_directory = directory / "output"
            work_directory = output_directory / "ytgrab-work.early"
            work_directory.mkdir(parents=True)
            (work_directory / "metadata.json").write_text("", encoding="utf-8")

            completed = self.run_bash(
                r'''
source "$TEST_REPOSITORY_ROOT/ytgrab.sh"
OUTDIR="$TEST_OUTPUT_DIRECTORY"
DUAL_WORKDIR="$TEST_WORK_DIRECTORY"
cleanup_unneeded_dual_workdir
[ -z "$DUAL_WORKDIR" ]
''',
                output_directory,
                work_directory,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(work_directory.exists())

    def test_cleanup_preserves_downloaded_files_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            output_directory = directory / "output"
            work_directory = output_directory / "ytgrab-work.partial"
            work_directory.mkdir(parents=True)
            (work_directory / "source.mkv.part").write_bytes(b"partial video")

            completed = self.run_bash(
                r'''
source "$TEST_REPOSITORY_ROOT/ytgrab.sh"
OUTDIR="$TEST_OUTPUT_DIRECTORY"
DUAL_WORKDIR="$TEST_WORK_DIRECTORY"
if cleanup_unneeded_dual_workdir; then
  exit 9
fi
[ -d "$DUAL_WORKDIR" ]
''',
                output_directory,
                work_directory,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(work_directory.is_dir())
            self.assertTrue((work_directory / "source.mkv.part").exists())

    def test_cleanup_clears_a_missing_workdir_without_reporting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            output_directory = directory / "output"
            output_directory.mkdir()
            work_directory = output_directory / "ytgrab-work.already-deleted"

            completed = self.run_bash(
                r'''
source "$TEST_REPOSITORY_ROOT/ytgrab.sh"
OUTDIR="$TEST_OUTPUT_DIRECTORY"
DUAL_WORKDIR="$TEST_WORK_DIRECTORY"
cleanup_unneeded_dual_workdir
[ -z "$DUAL_WORKDIR" ]
''',
                output_directory,
                work_directory,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(work_directory.exists())


if __name__ == "__main__":
    unittest.main()
