from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class BrowserCookieTests(unittest.TestCase):
    def run_ytdlp_wrapper(
        self,
        home: Path,
        fake_script: str,
        *,
        browser: str = "auto",
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        bin_directory = home.parent / "bin"
        bin_directory.mkdir()
        fake_ytdlp = bin_directory / "yt-dlp"
        fake_ytdlp.write_text(fake_script, encoding="utf-8")
        fake_ytdlp.chmod(0o755)
        log_file = home.parent / "yt-dlp.log"

        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "PATH": f"{bin_directory}{os.pathsep}{environment['PATH']}",
                "TEST_LOG": str(log_file),
                "TEST_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
                "YTGRAB_BROWSER": browser,
            }
        )
        completed = subprocess.run(
            [
                "bash",
                "-c",
                'source "$TEST_REPOSITORY_ROOT/ytgrab.sh"; '
                'run_ytdlp --simulate "https://example.invalid/video"',
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        lines = (
            log_file.read_text(encoding="utf-8").splitlines()
            if log_file.exists()
            else []
        )
        return completed, lines

    def test_auto_detects_safari_chrome_and_firefox_cookie_databases(self) -> None:
        cookie_paths = {
            "safari": Path("Library/Cookies/Cookies.binarycookies"),
            "chrome": Path(
                "Library/Application Support/Google/Chrome/Default/Network/Cookies"
            ),
            "firefox": Path(
                "Library/Application Support/Firefox/Profiles/test.default/cookies.sqlite"
            ),
        }

        for browser, relative_cookie_path in cookie_paths.items():
            with self.subTest(browser=browser), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                home = root / "home"
                cookie_file = home / relative_cookie_path
                cookie_file.parent.mkdir(parents=True)
                cookie_file.write_bytes(b"test")

                completed, calls = self.run_ytdlp_wrapper(
                    home,
                    '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$TEST_LOG"\nexit 0\n',
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(len(calls), 1)
                self.assertIn(
                    f"--cookies-from-browser {browser} --simulate",
                    calls[0],
                )

    def test_auto_uses_no_cookies_when_no_database_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            home.mkdir()

            completed, calls = self.run_ytdlp_wrapper(
                home,
                '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$TEST_LOG"\nexit 0\n',
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("--cookies-from-browser", calls[0])
            self.assertIn(
                "No browser cookie database found; trying YouTube without cookies.",
                completed.stderr,
            )

    def test_cookie_extraction_failure_retries_once_without_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            home.mkdir()

            fake_script = """#!/usr/bin/env bash
printf "%s\\n" "$*" >> "$TEST_LOG"
if [ "${1:-}" = "--cookies-from-browser" ]; then
  echo 'ERROR: could not find chrome cookies database in "/missing/Chrome"' >&2
  exit 1
fi
exit 0
"""
            completed, calls = self.run_ytdlp_wrapper(
                home,
                fake_script,
                browser="chrome",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(calls), 2)
            self.assertIn("--cookies-from-browser chrome", calls[0])
            self.assertNotIn("--cookies-from-browser", calls[1])
            self.assertIn(
                "Browser-cookie extraction failed; retrying without browser cookies...",
                completed.stderr,
            )


if __name__ == "__main__":
    unittest.main()
